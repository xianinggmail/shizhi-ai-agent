const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const py=process.env.STUDY_PYTHON||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe');
let browser;const servers=[];
async function service(port,mode){
 const child=spawn(py,['-B',path.join(root,'tests','serve_ai_fixture.py'),'--port',String(port),'--data-dir',path.join(root,'work',mode+'-'+Date.now()),'--mode',mode],{cwd:root,windowsHide:true,stdio:'ignore'});
 servers.push(child);const base='http://127.0.0.1:'+port;
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/config')).ok)return base;}catch{}await new Promise(r=>setTimeout(r,100));}
 throw Error('Fixture did not start');
}
async function begin(page,base){
 await page.goto(base);await page.locator('.mode-pill').getByText('DeepSeek 已配置',{exact:true}).waitFor();
 await page.locator('#pdfFile').setInputFiles(path.join(root,'work','演示参考书_合成测试材料.pdf'));
 await page.locator('#startStudy').waitFor();await page.fill('#endPage','6');await page.click('#startStudy');
}
(async()=>{
 const executable=await fs.access(chromium.executablePath()).then(()=>chromium.executablePath()).catch(()=> 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe');
 browser=await chromium.launch({headless:true,executablePath:executable,env:{...process.env,TEMP:path.join(root,'work'),TMP:path.join(root,'work')}});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const partial=await service(18769,'partial');
 await page.route('**/api/config',async route=>{const response=await route.fetch();const body=await response.json();delete body.local_recheck;delete body.generation_version;await route.fulfill({response,json:body});});
 await page.goto(partial);await page.locator('.mode-pill').getByText('DeepSeek 已配置',{exact:true}).waitFor();
 await page.locator('#pdfFile').setInputFiles(path.join(root,'work','演示参考书_合成测试材料.pdf'));
 await page.locator('#startStudy').waitFor();assert.equal(await page.locator('#startStudy').isDisabled(),true);
 assert.match(await page.locator('.banner').textContent(),/重启后使用新版核验/);
 await page.unroute('**/api/config');await page.click('#recheckAI');
 await page.waitForFunction(()=>!document.getElementById('startStudy').disabled);
 await page.fill('#endPage','6');await page.click('#startStudy');
 await page.locator('#reveal').waitFor();
 assert.match(await page.locator('#study').textContent(),/已保留 3 条可核验知识点，跳过 1 条/);
 await page.locator('#study summary').click();assert.match(await page.locator('#study details').textContent(),/第1条：返回的原文编号不存在/);
 await page.click('#reveal');await page.locator('.answer').waitFor();
 assert.match(await page.locator('.answer-text').textContent(),/测试响应/);
 assert.equal((await(await fetch(partial+'/api/config')).json()).calls,1);
 const invalid=await service(18770,'invalid');await begin(page,invalid);
 await page.locator('#recheckGeneration').waitFor();
 assert.match(await page.locator('#uploadProgress').textContent(),/没有卡片通过核验/);
 await page.locator('#uploadProgress summary').click();assert.match(await page.locator('#uploadProgress details').textContent(),/返回的原文编号不存在/);
 await page.screenshot({path:path.join(root,'work','diagnostic-ui.png'),fullPage:true});
 const postPromise=page.waitForResponse(r=>r.url()===invalid+'/api/jobs'&&r.request().method()==='POST');
 await page.click('#recheckGeneration');const post=await postPromise;const jid=(await post.json()).id;
 await page.waitForFunction(async id=>(await(await fetch('/api/jobs/'+id)).json()).status==='failed',jid);
 await page.locator('#recheckGeneration').waitFor();
 assert.equal((await(await fetch(invalid+'/api/config')).json()).calls,1,'Local recheck must not call the model');
 page.once('dialog',d=>d.dismiss());await page.click('#retryGeneration');
 assert.equal((await(await fetch(invalid+'/api/config')).json()).calls,1,'Cancelled paid retry must not call the model');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({result:'PASS',checks:['旧后台未重启时阻止误发请求','部分合格卡片正常学习','被跳过卡片逐条显示原因','全部失败有具体错误编号','免费本地核验不增加调用','付费重试可取消','无JavaScript错误'],note:'Only isolated mocked model responses; no real API calls.'},null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();for(const server of servers)server.kill();});
