# TF1 Generator

Generate app-compatible TF1 payloads for the laser fixture and optionally write them to `esp-proto/main/include/tf1_sample.h` so the ESP sender can transmit them without changing `main.c`.

## What You Can Define

1. Arbitrary line graphics via points (simple JSON format).
2. Full app `.seq` scene JSON imported directly (`sceneList` + `channelList` + `patternList`).

The generator uses the same TF1 payload layout used by the app serializer (`Z(...)`), including pattern encoding and scene/channel tables.

## Quick Start

```bash
python -m tf1_generator.cli \
  --input tf1_generator/examples/line_scene.json \
  --output tf1_generator/examples/line_scene.tf1 \
  --emit-header esp-proto/main/include/tf1_sample.h \
  --show-frames
```

Then flash/send with your existing ESP project.

## Input Formats

### 1) Simple Scene JSON (`scenes`)

```json
{
  "weight": 1,
  "device_type": "DQF6_LS01",
  "scenes": [
    {
      "time_ms": 5000,
      "play_mode": 0,
      "channels": [10, 40, 0, 0, 0, 0],
      "patterns": [
        {
          "close": true,
          "color": "#FFFFFF",
          "points": [[120,120], [240,120], [240,240], [120,240]]
        }
      ]
    }
  ]
}
```

Notes:
- `channels` can be partial; missing channels are filled from device defaults.
- CH1 should be non-zero (usually `10`) so output is not blacked out.
- Pattern index channel is set automatically from pattern table.

### 2) App `.seq` JSON (`sceneList`)

If the file contains `sceneList`, it is treated as app sequence format directly.

## Auto-Run Without DMX

Set scene channel values to autonomous output values in the payload (especially CH1/open-output and related effect channels). Since these values are embedded in TF1 scene data, fixture playback can run without external live DMX input.

## Useful Flags

- `--device-config WWW/static/DQF6_LS01_en.json`
- `--canvas-width 360 --canvas-height 360`
- `--emit-header esp-proto/main/include/tf1_sample.h`
- `--no-header` to only create `.tf1`
- `--show-frames` to print cmd17/cmd18 frame hex preview
