#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_err.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_bt_defs.h"
#include "esp_bt_device.h"
#include "esp_gap_ble_api.h"
#include "esp_gattc_api.h"
#include "esp_gatt_defs.h"

#include "tf1_sample.h"

#define TAG "esp_proto"
#define TARGET_NAME_PREFIX "NF-F2"
#define MAX_PENDING_CHUNK 600
#define MAX_SERVICE_RANGES 16
#define FRAME_HEAD 0xAA
#define FRAME_TAIL 0x5A
#define TF1_CMD_HANDSHAKE 17
#define TF1_CMD_CHUNK 18
#define WRITE_SLICE_SIZE 100
#define MIN(a, b) (((a) < (b)) ? (a) : (b))

static esp_ble_scan_params_t ble_scan_params = {
    .scan_type = BLE_SCAN_TYPE_ACTIVE,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .scan_filter_policy = BLE_SCAN_FILTER_ALLOW_ALL,
    .scan_interval = 0x50,
    .scan_window = 0x30,
    .scan_duplicate = BLE_SCAN_DUPLICATE_DISABLE,
};

static esp_gatt_if_t g_gattc_if = ESP_GATT_IF_NONE;
static bool g_scanning = false;
static bool g_connecting = false;
static bool g_connected = false;
static bool g_mtu_configured = false;
static bool g_should_connect = false;
static esp_bd_addr_t g_target_bda = {0};
static esp_ble_addr_type_t g_target_addr_type = BLE_ADDR_TYPE_PUBLIC;
static uint16_t g_conn_id = 0;

static uint16_t g_service_start = 0;
static uint16_t g_service_end = 0;
static uint16_t g_write_char_handle = 0;
static uint16_t g_notify_char_handle = 0;
static uint16_t g_ccc_handle = 0;
static bool g_notif_ready = false;

static uint32_t g_cache_length = 0;
static size_t g_chunk_size = 0;
static uint8_t g_seq = 1;
static size_t g_bytes_sent = 0;
static bool g_awaiting_chunk_ack = false;
static int g_retry_count = 0;
static size_t g_pending_data_len = 0;
static size_t g_pending_chunk_len = 0;
static uint8_t g_pending_chunk[MAX_PENDING_CHUNK];
static uint8_t g_rx_buf[1024];
static size_t g_rx_len = 0;
static size_t g_rx_expected_len = 0;

typedef struct {
    uint16_t start_handle;
    uint16_t end_handle;
    esp_bt_uuid_t uuid;
} service_range_t;

static service_range_t g_services[MAX_SERVICE_RANGES];
static size_t g_service_count = 0;

static void log_address(const char *label, const esp_bd_addr_t addr)
{
    char buffer[18];
    snprintf(buffer, sizeof(buffer), "%02X:%02X:%02X:%02X:%02X:%02X",
             addr[0], addr[1], addr[2], addr[3], addr[4], addr[5]);
    ESP_LOGI(TAG, "%s %s", label, buffer);
}

static void log_frame_hex(const char *label, const uint8_t *data, size_t len)
{
    char buffer[96];
    size_t offset = 0;
    for (size_t i = 0; i < len && offset + 3 < sizeof(buffer); ++i) {
        offset += snprintf(&buffer[offset], sizeof(buffer) - offset, "%02X", data[i]);
        if (i + 1 != len && offset + 1 < sizeof(buffer)) {
            buffer[offset++] = ' ';
        }
    }
    buffer[offset < sizeof(buffer) ? offset : sizeof(buffer) - 1] = '\0';
    ESP_LOGI(TAG, "%s: %s", label, buffer);
}

static bool adv_has_target_prefix(const esp_ble_gap_cb_param_t *param)
{
    const size_t prefix_len = sizeof(TARGET_NAME_PREFIX) - 1;
    uint8_t name_len = 0;
    uint8_t *adv_name = esp_ble_resolve_adv_data(
        (uint8_t *)param->scan_rst.ble_adv, ESP_BLE_AD_TYPE_NAME_CMPL, &name_len);
    if (adv_name && name_len >= prefix_len &&
        !memcmp(adv_name, TARGET_NAME_PREFIX, prefix_len)) {
        return true;
    }
    adv_name = esp_ble_resolve_adv_data(
        (uint8_t *)param->scan_rst.ble_adv, ESP_BLE_AD_TYPE_NAME_SHORT, &name_len);
    if (adv_name && name_len >= prefix_len &&
        !memcmp(adv_name, TARGET_NAME_PREFIX, prefix_len)) {
        return true;
    }
    return false;
}

static void start_scan(void)
{
    if (g_scanning) {
        return;
    }
    esp_err_t err = esp_ble_gap_start_scanning(0);
    if (err == ESP_OK) {
        g_scanning = true;
        ESP_LOGI(TAG, "Started BLE scan");
    } else {
        ESP_LOGE(TAG, "Failed to start scan: %s", esp_err_to_name(err));
    }
}

static esp_err_t write_frame(const uint8_t *frame, size_t len)
{
    if (!g_connected || g_write_char_handle == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    size_t offset = 0;
    while (offset < len) {
        size_t slice_len = MIN(WRITE_SLICE_SIZE, len - offset);
        esp_err_t err = esp_ble_gattc_write_char(
            g_gattc_if,
            g_conn_id,
            g_write_char_handle,
            slice_len,
            (uint8_t *)&frame[offset],
            ESP_GATT_WRITE_TYPE_NO_RSP,
            ESP_GATT_AUTH_REQ_NONE);
        if (err != ESP_OK) {
            return err;
        }
        offset += slice_len;
        if (offset < len) {
            vTaskDelay(pdMS_TO_TICKS(20));
        }
    }
    return ESP_OK;
}

static void reset_transfer_state(void)
{
    g_cache_length = 0;
    g_chunk_size = 0;
    g_seq = 1;
    g_bytes_sent = 0;
    g_awaiting_chunk_ack = false;
    g_retry_count = 0;
    g_pending_chunk_len = 0;
    g_pending_data_len = 0;
    g_rx_len = 0;
    g_rx_expected_len = 0;
}

static bool is_standard_service(const esp_bt_uuid_t *uuid)
{
    if (uuid->len != ESP_UUID_LEN_16) {
        return false;
    }
    return uuid->uuid.uuid16 == 0x1800 || uuid->uuid.uuid16 == 0x1801;
}

static bool select_chars_for_service(uint16_t start_handle, uint16_t end_handle,
                                     uint16_t *write_handle, uint16_t *notify_handle)
{
    *write_handle = 0;
    *notify_handle = 0;

    uint16_t count = 0;
    esp_err_t err = esp_ble_gattc_get_attr_count(
        g_gattc_if, g_conn_id, ESP_GATT_DB_CHARACTERISTIC,
        start_handle, end_handle, ESP_GATT_ILLEGAL_HANDLE, &count);
    if (err != ESP_OK || count == 0) {
        return false;
    }

    esp_gattc_char_elem_t *chars = calloc(count, sizeof(esp_gattc_char_elem_t));
    if (chars == NULL) {
        ESP_LOGE(TAG, "Characteristic list allocation failed");
        return false;
    }

    err = esp_ble_gattc_get_all_char(g_gattc_if, g_conn_id,
                                     start_handle, end_handle, chars, &count, 0);
    if (err != ESP_OK) {
        free(chars);
        return false;
    }

    for (int i = 0; i < count; ++i) {
        if ((*notify_handle == 0) &&
            (chars[i].properties & (ESP_GATT_CHAR_PROP_BIT_NOTIFY | ESP_GATT_CHAR_PROP_BIT_INDICATE))) {
            *notify_handle = chars[i].char_handle;
        }
        if ((*write_handle == 0) &&
            (chars[i].properties & (ESP_GATT_CHAR_PROP_BIT_WRITE | ESP_GATT_CHAR_PROP_BIT_WRITE_NR))) {
            *write_handle = chars[i].char_handle;
        }
    }

    free(chars);
    return *write_handle != 0;
}

static void queue_next_chunk(void);

static void send_handshake_frame(void)
{
    uint8_t frame[16];
    frame[0] = FRAME_HEAD;
    frame[1] = TF1_CMD_HANDSHAKE;
    frame[2] = 0;
    frame[3] = FRAME_TAIL;
    frame[4] = 16;
    frame[5] = 0;
    frame[6] = 0;
    frame[7] = 0;
    frame[8] = 'T';
    frame[9] = 'F';
    frame[10] = '1';
    frame[11] = 0;
    uint32_t total_len = (uint32_t)sample_tf1_payload_len;
    frame[12] = total_len & 0xFF;
    frame[13] = (total_len >> 8) & 0xFF;
    frame[14] = (total_len >> 16) & 0xFF;
    frame[15] = (total_len >> 24) & 0xFF;
    log_frame_hex("handshake", frame, sizeof(frame));
    esp_err_t err = write_frame(frame, sizeof(frame));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send handshake: %s", esp_err_to_name(err));
    } else {
    }
}

static void resend_pending_chunk(void)
{
    if (!g_awaiting_chunk_ack || g_pending_chunk_len == 0) {
        return;
    }
    g_retry_count++;
    if (g_retry_count > 3) {
        ESP_LOGE(TAG, "Chunk retry limit reached; giving up");
        return;
    }
    ESP_LOGW(TAG, "Resending chunk seq %u (attempt %d)", g_seq, g_retry_count);
    log_frame_hex("chunk resend", g_pending_chunk, g_pending_chunk_len);
    esp_err_t err = write_frame(g_pending_chunk, g_pending_chunk_len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Chunk resend error: %s", esp_err_to_name(err));
    }
}

static void queue_next_chunk(void)
{
    if (g_chunk_size == 0) {
        ESP_LOGW(TAG, "Chunk size is zero, cannot send payload");
        return;
    }
    if (g_bytes_sent >= sample_tf1_payload_len) {
        ESP_LOGI(TAG, "TF1 payload already transmitted");
        return;
    }
    size_t remaining = sample_tf1_payload_len - g_bytes_sent;
    size_t chunk_len = MIN(g_chunk_size, remaining);
    size_t header_len = 12;
    size_t frame_len = header_len + chunk_len;
    if (frame_len > sizeof(g_pending_chunk)) {
        ESP_LOGE(TAG, "Chunk frame %u bytes exceeds buffer", frame_len);
        return;
    }
    g_pending_chunk[0] = FRAME_HEAD;
    g_pending_chunk[1] = TF1_CMD_CHUNK;
    g_pending_chunk[2] = 0;
    g_pending_chunk[3] = FRAME_TAIL;
    g_pending_chunk[4] = frame_len & 0xFF;
    g_pending_chunk[5] = (frame_len >> 8) & 0xFF;
    g_pending_chunk[6] = g_seq & 0xFF;
    g_pending_chunk[7] = (g_seq >> 8) & 0xFF;
    g_pending_chunk[8] = 'T';
    g_pending_chunk[9] = 'F';
    g_pending_chunk[10] = '1';
    g_pending_chunk[11] = 0;
    memcpy(&g_pending_chunk[12], &sample_tf1_payload[g_bytes_sent], chunk_len);
    g_pending_chunk_len = frame_len;
    g_pending_data_len = chunk_len;
    g_awaiting_chunk_ack = true;
    g_retry_count = 0;
    log_frame_hex("chunk", g_pending_chunk, g_pending_chunk_len);
    esp_err_t err = write_frame(g_pending_chunk, g_pending_chunk_len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send chunk: %s", esp_err_to_name(err));
    }
}

static void handle_device_ack(const uint8_t *value, uint16_t value_len)
{
    if (value_len < 10) {
        ESP_LOGW(TAG, "Ignored short notification (%u bytes)", value_len);
        return;
    }
    uint8_t cmd = value[1];
    uint8_t status = value[6];
    if (cmd == 17) {
        if (status != 0) {
            ESP_LOGE(TAG, "Fixture rejected handshake (status=%u)", status);
            return;
        }
        g_cache_length = ((uint16_t)value[8]) | ((uint16_t)value[9] << 8);
        ESP_LOGI(TAG, "Handshake ack: cache_length=%u", g_cache_length);
        if (g_cache_length <= 12) {
            ESP_LOGE(TAG, "Cache length (%u) too small", g_cache_length);
            return;
        }
        g_chunk_size = g_cache_length > 12 ? g_cache_length - 12 : 0;
        g_seq = 1;
        g_bytes_sent = 0;
        g_awaiting_chunk_ack = false;
        queue_next_chunk();
    } else if (cmd == 18) {
        if (status == 0) {
            g_awaiting_chunk_ack = false;
            g_bytes_sent += g_pending_data_len;
            ESP_LOGI(TAG, "Chunk %u acked (%u/%u bytes)",
                     g_seq, g_bytes_sent, sample_tf1_payload_len);
            if (g_bytes_sent >= sample_tf1_payload_len) {
                ESP_LOGI(TAG, "TF1 payload transfer complete");
                return;
            }
            g_seq++;
            queue_next_chunk();
        } else {
            ESP_LOGW(TAG, "Fixture reported chunk failure (status=%u)", status);
            resend_pending_chunk();
        }
    } else {
        ESP_LOGW(TAG, "Unhandled fixture command %u", cmd);
    }
}

static void process_notify_fragment(const uint8_t *value, uint16_t value_len)
{
    if (value == NULL || value_len == 0) {
        return;
    }
    log_frame_hex("notify fragment", value, value_len);

    if (g_rx_len + value_len > sizeof(g_rx_buf)) {
        ESP_LOGW(TAG, "RX buffer overflow; dropping partial frame");
        g_rx_len = 0;
        g_rx_expected_len = 0;
        return;
    }

    memcpy(&g_rx_buf[g_rx_len], value, value_len);
    g_rx_len += value_len;

    if (g_rx_expected_len == 0 && g_rx_len >= 6) {
        g_rx_expected_len = ((size_t)g_rx_buf[5] << 8) | g_rx_buf[4];
        g_rx_expected_len += 2;
        if (g_rx_expected_len > sizeof(g_rx_buf)) {
            ESP_LOGW(TAG, "Invalid RX expected length: %u", (unsigned)g_rx_expected_len);
            g_rx_len = 0;
            g_rx_expected_len = 0;
            return;
        }
    }

    if (g_rx_expected_len > 0 && g_rx_len >= g_rx_expected_len) {
        handle_device_ack(g_rx_buf, (uint16_t)g_rx_expected_len);
        g_rx_len = 0;
        g_rx_expected_len = 0;
    }
}

static void gap_event_handler(esp_gap_ble_cb_event_t event,
                              esp_ble_gap_cb_param_t *param)
{
    switch (event) {
    case ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT:
        start_scan();
        break;
    case ESP_GAP_BLE_SCAN_START_COMPLETE_EVT:
        if (param->scan_start_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            ESP_LOGE(TAG, "Scan start failed (%d)", param->scan_start_cmpl.status);
        }
        break;
    case ESP_GAP_BLE_SCAN_RESULT_EVT:
        if (param->scan_rst.search_evt == ESP_GAP_SEARCH_INQ_RES_EVT &&
            !g_connected && !g_connecting &&
            adv_has_target_prefix(param)) {
            log_address("Found target device", param->scan_rst.bda);
            memcpy(g_target_bda, param->scan_rst.bda, sizeof(esp_bd_addr_t));
            g_target_addr_type = param->scan_rst.ble_addr_type;
            g_connecting = true;
            g_should_connect = true;
            esp_ble_gap_stop_scanning();
        }
        break;
    case ESP_GAP_BLE_SCAN_STOP_COMPLETE_EVT:
        g_scanning = false;
        if (param->scan_stop_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            ESP_LOGE(TAG, "Scan stop failed (%d)", param->scan_stop_cmpl.status);
        }
        if (g_should_connect && g_gattc_if != ESP_GATT_IF_NONE) {
            esp_err_t err = esp_ble_gattc_open(g_gattc_if, g_target_bda, g_target_addr_type, true);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "Failed to open connection: %s", esp_err_to_name(err));
                g_connecting = false;
                start_scan();
            }
            g_should_connect = false;
        }
        break;
    default:
        break;
    }
}

static void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                                esp_ble_gattc_cb_param_t *param)
{
    switch (event) {
    case ESP_GATTC_REG_EVT:
        if (param->reg.status == ESP_GATT_OK) {
            g_gattc_if = gattc_if;
            ESP_LOGI(TAG, "GATTC registered, interface=%d", gattc_if);
        }
        break;
    case ESP_GATTC_OPEN_EVT:
        if (param->open.status == ESP_GATT_OK) {
            g_conn_id = param->open.conn_id;
            g_connected = true;
            g_connecting = false;
            g_mtu_configured = false;
            g_service_count = 0;
            memcpy(g_target_bda, param->open.remote_bda, sizeof(g_target_bda));
            log_address("Connected to", param->open.remote_bda);
            esp_err_t mtu_err = esp_ble_gattc_send_mtu_req(gattc_if, g_conn_id);
            if (mtu_err != ESP_OK) {
                ESP_LOGW(TAG, "MTU request failed (%s), continue with default MTU",
                         esp_err_to_name(mtu_err));
                esp_ble_gattc_search_service(gattc_if, g_conn_id, NULL);
            }
        } else {
            ESP_LOGE(TAG, "Connection failed: %d", param->open.status);
            g_connecting = false;
            start_scan();
        }
        break;
    case ESP_GATTC_CFG_MTU_EVT:
        if (param->cfg_mtu.status == ESP_GATT_OK) {
            g_mtu_configured = true;
            ESP_LOGI(TAG, "Configured MTU=%u", param->cfg_mtu.mtu);
        } else {
            ESP_LOGW(TAG, "MTU config failed: %d", param->cfg_mtu.status);
        }
        esp_ble_gattc_search_service(gattc_if, g_conn_id, NULL);
        break;
    case ESP_GATTC_SEARCH_RES_EVT:
        if (g_service_count < MAX_SERVICE_RANGES) {
            g_services[g_service_count].start_handle = param->search_res.start_handle;
            g_services[g_service_count].end_handle = param->search_res.end_handle;
            g_services[g_service_count].uuid = param->search_res.srvc_id.uuid;
            ESP_LOGI(TAG, "Service[%d] range %04X..%04X",
                     (int)g_service_count,
                     g_services[g_service_count].start_handle,
                     g_services[g_service_count].end_handle);
            g_service_count++;
        }
        break;
    case ESP_GATTC_SEARCH_CMPL_EVT:
        if (param->search_cmpl.status != ESP_GATT_OK || g_service_count == 0) {
            ESP_LOGE(TAG, "Service search failed");
            break;
        }
        {
            uint16_t fallback_service_start = 0;
            uint16_t fallback_service_end = 0;
            uint16_t fallback_write = 0;
            uint16_t fallback_notify = 0;

            for (int pass = 0; pass < 2 && g_write_char_handle == 0; ++pass) {
                for (size_t i = 0; i < g_service_count; ++i) {
                    bool standard = is_standard_service(&g_services[i].uuid);
                    if ((pass == 0 && standard) || (pass == 1 && !standard)) {
                        continue;
                    }
                    uint16_t write_handle = 0;
                    uint16_t notify_handle = 0;
                    if (!select_chars_for_service(g_services[i].start_handle,
                                                  g_services[i].end_handle,
                                                  &write_handle, &notify_handle)) {
                        continue;
                    }
                    if (write_handle != 0 && notify_handle != 0) {
                        g_service_start = g_services[i].start_handle;
                        g_service_end = g_services[i].end_handle;
                        g_write_char_handle = write_handle;
                        g_notify_char_handle = notify_handle;
                        break;
                    }
                    if (fallback_write == 0 && write_handle != 0) {
                        fallback_service_start = g_services[i].start_handle;
                        fallback_service_end = g_services[i].end_handle;
                        fallback_write = write_handle;
                        fallback_notify = notify_handle;
                    }
                }
            }

            if (g_write_char_handle == 0 && fallback_write != 0) {
                g_service_start = fallback_service_start;
                g_service_end = fallback_service_end;
                g_write_char_handle = fallback_write;
                g_notify_char_handle = fallback_notify;
            }

            if (g_write_char_handle == 0) {
                ESP_LOGE(TAG, "No write-capable characteristic found");
                break;
            }

            ESP_LOGI(TAG, "Selected service %04X..%04X write=0x%04X notify=0x%04X",
                     g_service_start, g_service_end, g_write_char_handle, g_notify_char_handle);

            if (g_notify_char_handle != 0) {
                esp_ble_gattc_register_for_notify(
                    gattc_if, g_target_bda, g_notify_char_handle);
            } else {
                ESP_LOGE(TAG, "No notify/indicate characteristic in selected service");
            }
        }
        break;
    case ESP_GATTC_REG_FOR_NOTIFY_EVT:
        if (param->reg_for_notify.status != ESP_GATT_OK) {
            ESP_LOGE(TAG, "Register for notify failed");
            break;
        }
        if (g_notify_char_handle == 0) {
            break;
        }
        {
            uint16_t count = 0;
            if (g_service_end == 0) {
                break;
            }
            esp_err_t err = esp_ble_gattc_get_attr_count(
                gattc_if, g_conn_id, ESP_GATT_DB_DESCRIPTOR,
                g_service_start, g_service_end,
                g_notify_char_handle, &count);
            if (err != ESP_OK || count == 0) {
                ESP_LOGE(TAG, "Descriptor count failed");
                break;
            }
            esp_gattc_descr_elem_t *descrs =
                calloc(count, sizeof(esp_gattc_descr_elem_t));
            if (descrs == NULL) {
                ESP_LOGE(TAG, "Descriptor allocation failed");
                break;
            }
            err = esp_ble_gattc_get_all_descr(
                gattc_if, g_conn_id, g_notify_char_handle,
                descrs, &count, 0);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "Descriptor read failed");
                free(descrs);
                break;
            }
            for (int i = 0; i < count; ++i) {
                if (descrs[i].uuid.len == ESP_UUID_LEN_16 &&
                    descrs[i].uuid.uuid.uuid16 == ESP_GATT_UUID_CHAR_CLIENT_CONFIG) {
                    g_ccc_handle = descrs[i].handle;
                    break;
                }
            }
            free(descrs);
            if (g_ccc_handle == 0) {
                ESP_LOGE(TAG, "Could not find CCC descriptor");
                break;
            }
            uint8_t notify_en[2] = {0x01, 0x00};
            esp_ble_gattc_write_char_descr(
                gattc_if,
                g_conn_id,
                g_ccc_handle,
                sizeof(notify_en),
                notify_en,
                ESP_GATT_WRITE_TYPE_RSP,
                ESP_GATT_AUTH_REQ_NONE);
        }
        break;
    case ESP_GATTC_WRITE_DESCR_EVT:
        if (param->write.handle == g_ccc_handle &&
            param->write.status == ESP_GATT_OK) {
            g_notif_ready = true;
            reset_transfer_state();
            send_handshake_frame();
        }
        break;
    case ESP_GATTC_NOTIFY_EVT:
        process_notify_fragment(param->notify.value, param->notify.value_len);
        break;
    case ESP_GATTC_WRITE_CHAR_EVT:
        if (param->write.status != ESP_GATT_OK) {
            ESP_LOGE(TAG, "Write error: %d", param->write.status);
        }
        break;
    case ESP_GATTC_DISCONNECT_EVT:
    case ESP_GATTC_CLOSE_EVT:
        g_connected = false;
        g_connecting = false;
        g_mtu_configured = false;
        g_notif_ready = false;
        g_service_count = 0;
        g_service_start = g_service_end = 0;
        g_write_char_handle = g_notify_char_handle = g_ccc_handle = 0;
        reset_transfer_state();
        ESP_LOGI(TAG, "Disconnected, restarting scan");
        start_scan();
        break;
    default:
        break;
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT);
    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bt_controller_init(&bt_cfg));
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_BLE));
    ESP_ERROR_CHECK(esp_bluedroid_init());
    ESP_ERROR_CHECK(esp_bluedroid_enable());

    ESP_ERROR_CHECK(esp_ble_gattc_register_callback(gattc_event_handler));
    ESP_ERROR_CHECK(esp_ble_gap_register_callback(gap_event_handler));
    ESP_ERROR_CHECK(esp_ble_gattc_app_register(0));
    ESP_ERROR_CHECK(esp_ble_gap_set_scan_params(&ble_scan_params));
}
