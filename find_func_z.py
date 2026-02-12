import pathlib
path=pathlib.Path('WWW/app-service.js')
data=path.read_text(encoding='utf-8')
needle='function Z('
idx=data.find(needle)
print(idx)
if idx!=-1:
    start=max(idx-200,0)
    end=min(idx+400,len(data))
    print(data[start:end])
