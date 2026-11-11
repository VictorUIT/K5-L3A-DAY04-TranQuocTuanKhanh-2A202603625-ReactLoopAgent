"""Kiểm tra agent với tools bonus mà không gọi LLM API."""
import json
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent import run_agent
from bonus.tools import BonusTools


def call(call_id,name,args):
    raw=json.dumps(args)
    c={'id':call_id,'name':name,'arguments':raw}
    m={'role':'assistant','content':None,'tool_calls':[{'id':call_id,'type':'function','function':{'name':name,'arguments':raw}}]}
    return {'message':m,'calls':[c],'finish':'tool_calls','usage':{}}

def final(answer='Đã kiểm tra'):
    return {'message':{'role':'assistant','content':answer},'calls':[],'finish':'stop','usage':{}}


class ScriptedProvider:
    def __init__(self,responses): self.responses=iter(responses); self.histories=[]; self.schemas=[]
    def complete(self,messages,schemas):
        self.histories.append(list(messages));self.schemas.append(schemas)
        return next(self.responses)


class BonusAgentTests(unittest.TestCase):
    def test_bonus_schemas_and_system_prompt(self):
        t=BonusTools();p=ScriptedProvider([final()])
        self.assertEqual(run_agent('Kiểm tra',p,t)['status'],'completed')
        names={x['function']['name'] for x in p.schemas[0]}
        self.assertTrue({'get_github_status','get_cloudflare_status','get_vercel_status','get_npm_status','get_digitalocean_status'}.issubset(names))
        self.assertIn('DigitalOcean',p.histories[0][0]['content'])
    def test_429_observation_returns_to_model(self):
        t=BonusTools(scenario='rate_limit_once')
        p=ScriptedProvider([call('a','get_vercel_status',{}),call('b','get_vercel_status',{}),final()])
        result=run_agent('Kiểm tra',p,t,max_steps=5)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(json.loads(p.histories[1][-1]['content'])['error'],'RATE_LIMITED')
        self.assertEqual(json.loads(p.histories[2][-1]['content'])['provider'],'vercel')
    def test_invalid_arguments_observation_returns_to_model(self):
        t=BonusTools()
        bad={'provider':'npm','incident_id':'cc0t2r4403sy','offset':'zero'}
        good={'provider':'npm','incident_id':'cc0t2r4403sy','offset':0}
        details=t.execute('get_incident_details',good)
        p=ScriptedProvider([call('a','get_incident_details',bad),call('b','get_incident_details',good),final('Nguồn: '+details['url'])])
        result=run_agent('Kiểm tra',p,t,max_steps=5)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(json.loads(p.histories[1][-1]['content'])['error'],'INVALID_ARGUMENTS')
        self.assertTrue(json.loads(p.histories[2][-1]['content'])['ok'])
    def test_persistent_rate_limit_stops_after_three_tool_calls(self):
        responses=[call(str(i),'get_vercel_status',{}) for i in range(4)]
        responses.append(final('Vercel bị rate limit; chưa xác định được trạng thái.'))
        tools=BonusTools(scenario='rate_limit_always')
        provider=ScriptedProvider(responses)
        result=run_agent('Kiểm tra Vercel',provider,tools,max_steps=6)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(tools.calls[('get_vercel_status','vercel')],3)
        observations=[json.loads(history[-1]['content']) for history in provider.histories[1:]]
        self.assertEqual(observations[-1]['error'],'RETRY_BUDGET_EXHAUSTED')
    def test_noisy_distractors_are_omitted_from_model_context(self):
        provider=ScriptedProvider([
            call('a','get_vercel_status',{}),
            final('Vercel snapshot: https://www.vercel-status.com'),
        ])
        result=run_agent('Kiểm tra Vercel',provider,BonusTools(scenario='noisy'))
        self.assertEqual(result['status'],'completed')
        observation=json.loads(provider.histories[1][-1]['content'])
        self.assertEqual(observation['distractors_omitted'],160)
        self.assertNotIn('distractors',observation)
        tool_event=next(x for x in result['trace'] if x['event']=='tool')
        self.assertEqual(tool_event['result']['distractors_omitted'],160)
    def test_incident_pagination_arguments_are_forwarded(self):
        first={'provider':'vercel','incident_id':'sps6n39gm4sg','offset':0,'limit':3}
        second={'provider':'vercel','incident_id':'sps6n39gm4sg','offset':3,'limit':3}
        details=BonusTools()
        second_page=details.execute('get_incident_details',second)
        answer='Timeline: '+second_page['url']
        provider=ScriptedProvider([
            call('a','get_incident_details',first),
            call('b','get_incident_details',second),
            final(answer),
        ])
        result=run_agent('Đọc timeline Vercel',provider,details)
        self.assertEqual(result['status'],'completed')
        tool_events=[x for x in result['trace'] if x['event']=='tool']
        self.assertEqual([x['arguments']['offset'] for x in tool_events],[0,3])
        self.assertTrue(any('GitHub-connected' in u['body'] for u in tool_events[1]['result']['updates']))
    def test_long_retry_after_stops_without_another_execution(self):
        class LongRetryTools:
            schemas=[]
            system_prompt='test'
            def __init__(self): self.calls=0
            def execute(self,name,args):
                self.calls+=1
                return {'ok':False,'error':'RATE_LIMITED','retry_after_seconds':30}
        tools=LongRetryTools()
        provider=ScriptedProvider([
            call('a','get_vercel_status',{}),
            call('b','get_vercel_status',{}),
            final('Không thể xác định trạng thái Vercel.'),
        ])
        result=run_agent('Kiểm tra Vercel',provider,tools)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(tools.calls,1)
        observation=json.loads(provider.histories[1][-1]['content'])
        self.assertTrue(observation['retry_exhausted'])
    def test_persistent_invalid_arguments_are_bounded(self):
        class InvalidTools:
            schemas=[]
            system_prompt='test'
            def __init__(self): self.calls=0
            def execute(self,name,args):
                self.calls+=1
                return {'ok':False,'error':'INVALID_ARGUMENTS','message':'invalid'}
        tools=InvalidTools()
        responses=[call(str(i),'get_vercel_status',{}) for i in range(4)]
        responses.append(final('Không thể kiểm tra do tham số không hợp lệ.'))
        provider=ScriptedProvider(responses)
        result=run_agent('Kiểm tra Vercel',provider,tools,max_steps=6)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(tools.calls,3)
        self.assertEqual(
            json.loads(provider.histories[4][-1]['content'])['error'],
            'RETRY_BUDGET_EXHAUSTED',
        )

if __name__=='__main__': unittest.main()
