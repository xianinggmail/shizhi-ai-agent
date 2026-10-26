"""Test only. All paper responses synthetic; no key or network transport."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,make_server
from deepseek_ai import DeepSeekExtractor
from test_papers import fixture_read,fixture_quiz,fixture_model,response
p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);p.add_argument('--data-dir',required=True);p.add_argument('--mode',default='happy');args=p.parse_args();calls=0
def transport(payload,key):
    global calls
    calls+=1;content=json.loads(payload['messages'][1]['content'])
    result=fixture_model(content)
    if args.mode=='missing-essays' and content['stage']=='quiz' and content['request']['kind']=='both':result['essays']=[]
    if args.mode=='broken-once' and calls==1:
        for point in result['points']:point['source_ids']=['missing']
    return response(result)
store=Store(args.data_dir,extractor=DeepSeekExtractor(Path(__file__).resolve().parents[1],transport=transport,key_loader=lambda:'synthetic-only'))
server=make_server(store,args.port)
try:server.serve_forever()
finally:server.server_close();store.pool.shutdown(wait=True);store.job_pool.shutdown(wait=True)
