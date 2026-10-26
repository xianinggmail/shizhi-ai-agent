const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const py=process.env.STUDY_PYTHON||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe');
let browser,fixture,real;
async function ready(url){for(let i=0;i<100;i++){try{if((await fetch(url+'/api/config')).ok)return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('service not ready');}
(async()=>{
 const executable=await fs.access(chromium.executablePath()).then(()=>chromium.executablePath()).catch(()=> 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe');
 browser=await chromium.launch({headless:true,executablePath:executable,env:{...process.env,TEMP:path.join(root,'work'),TMP:path.join(root,'work')}});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 real=spawn(py,['-B',path.join(root,'app.py'),'--port','18767','--data-dir',path.join(root,'work','no-key-ui-'+Date.now())],{cwd:root,windowsHide:true,stdio:'ignore',env:{...process.env,DEEPSEEK_API_KEY:''}});
 await ready('http://127.0.0.1:18767');await page.goto('http://127.0.0.1:18767');
 await page.locator('.mode-pill').getByText('DeepSeek 待配置',{exact:true}).waitFor();
 await page.locator('#pdfFile').setInputFiles(path.join(root,'work','演示参考书_合成测试材料.pdf'));
 await page.locator('#startStudy').waitFor();
 assert.equal(await page.locator('#startStudy').isDisabled(),true);
 assert.match(await page.locator('#rangePanel').textContent(),/尚未配置/);
 await page.click('#recheckAI');
 await page.locator('.mode-pill').getByText('DeepSeek 待配置',{exact:true}).waitFor();
 const publicConfig=await (await fetch('http://127.0.0.1:18767/api/config')).json();
 assert.equal(publicConfig.ready,false);assert.equal(publicConfig.mode,'deepseek');assert.equal(publicConfig.api_key,undefined);
 fixture=spawn(py,['-B',path.join(root,'tests','serve_ai_fixture.py'),'--port','18768','--data-dir',path.join(root,'work','mock-ai-ui-'+Date.now())],{cwd:root,windowsHide:true,stdio:'ignore'});
 await ready('http://127.0.0.1:18768');await page.goto('http://127.0.0.1:18768');
 await page.locator('.mode-pill').getByText('DeepSeek 已配置',{exact:true}).waitFor();
 await page.locator('#pdfFile').setInputFiles(path.join(root,'work','演示参考书_合成测试材料.pdf'));
 await page.locator('#startStudy').waitFor();
 await page.fill('#endPage','6');await page.click('#startStudy');
 await page.locator('#reveal').waitFor();
 assert.match(await page.locator('.card-tag').last().textContent(),/AI提炼/);
 assert.equal(await page.locator('.answer').count(),0);
 await page.fill('#recallInput','这是自动化测试中的回忆草稿。');
 await page.click('#reveal');await page.locator('.answer-text').waitFor();
 assert.match(await page.locator('.answer-text').textContent(),/测试响应/);
 assert.match(await page.locator('.answer').textContent(),/学习理由/);
 assert.match(await page.locator('.answer').textContent(),/非考试概率/);
 await page.click('#checkSource');await page.locator('#sourceDialog[open]').waitFor();await page.click('#closeSource');
 for(let i=0;i<4;i++){
   if(i){await page.locator('#reveal').waitFor();await page.click('#reveal');}
   await page.click('[data-rating="mastered"]');
 }
 await page.locator('#finishHome').waitFor();await page.click('#finishHome');
 await page.click('#libraryNav');
 await page.fill('#endPage','6');await page.click('#startStudy');
 await page.getByText('生成未完成',{exact:true}).waitFor();
 const config=await(await fetch('http://127.0.0.1:18768/api/config')).json();
 assert.equal(config.calls,1);assert.equal(config.total_tokens,200);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({result:'PASS',checks:['默认真实模式与缺密钥提示','无密钥禁止生成','重新检查配置','前端无密钥泄露','后台生成并进入学习','答案默认隐藏','AI解释与出处区分','学习理由与非考试优先级','源页核对','四条学习自评','已学范围缓存不重付费','无JavaScript异常'],note:'Model responses were mocked in an isolated test server. No real DeepSeek call.'},null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(real)real.kill();if(fixture)fixture.kill();});
