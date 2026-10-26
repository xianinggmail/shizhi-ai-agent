"""TEST ONLY: mocked DeepSeek transport; no remote service and no credentials."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store, make_server
from deepseek_ai import DeepSeekExtractor
from test_deepseek import valid_response, request_pages
import json

p=argparse.ArgumentParser()
p.add_argument('--port',type=int,required=True)
p.add_argument('--data-dir',required=True)
p.add_argument('--mode',default='clean',choices=['clean','partial','invalid'])
args=p.parse_args()
def fake_transport(payload,key):
    pages=request_pages(payload)
    result=valid_response(pages)
    if args.mode!='clean':
        data=json.loads(result['choices'][0]['message']['content'])
        indices=[0] if args.mode=='partial' else range(len(data['cards']))
        for index in indices:data['cards'][index]['source_id']='missing-id'
        result['choices'][0]['message']['content']=json.dumps(data,ensure_ascii=False)
    return result
ai=DeepSeekExtractor(Path(__file__).resolve().parents[1],transport=fake_transport,key_loader=lambda:'synthetic-only')
store=Store(args.data_dir,extractor=ai)
server=make_server(store,args.port)
try:server.serve_forever()
finally:
    server.server_close()
    store.pool.shutdown(wait=True)
    store.job_pool.shutdown(wait=True)
