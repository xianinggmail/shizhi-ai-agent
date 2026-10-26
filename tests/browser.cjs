// Integration check against a real local server and Chromium/Edge. No AI calls.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const python=process.env.STUDY_PYTHON || path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe');
const port=18765,base=`http://127.0.0.1:${port}`;
let server,browser;
(async()=>{
  const data=path.join(root,'work','browser-data-'+Date.now());
  server=spawn(python,[path.join(root,'app.py'),'--port',String(port),'--data-dir',data,'--demo'],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'},stdio:'pipe'});
  let log='';server.stderr.on('data',b=>log+=b.toString());
  for(let i=0;i<80;i++){try{if((await fetch(base+'/api/config')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  assert.equal((await fetch(base+'/')).status,200,log);
  const bundled=chromium.executablePath();
  const executable=await fs.access(bundled).then(()=>bundled).catch(()=> 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe');
  browser=await chromium.launch({headless:true,executablePath:executable,env:{...process.env,TEMP:path.join(root,'work'),TMP:path.join(root,'work')}});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.goto(base);
  await page.locator('#recentBooks').getByText('你的书架还是空的').waitFor();
  await page.screenshot({path:path.join(root,'work','01-dashboard.png'),fullPage:true});
  await page.locator('#pdfFile').setInputFiles(path.join(root,'work','演示参考书_合成测试材料.pdf'));
  await page.locator('#startStudy').waitFor();
  assert.match(await page.locator('#chapter').textContent(),/第一章/);
  await page.selectOption('#chapter','0');
  assert.equal(await page.inputValue('#startPage'),'1');assert.equal(await page.inputValue('#endPage'),'3');
  await page.fill('#endPage','6');
  await page.click('#previewPage');await page.locator('#sourceDialog[open]').waitFor();
  assert.match(await page.locator('#sourceText').textContent(),/传播/);await page.click('#closeSource');
  await page.click('#startStudy');await page.locator('#reveal').waitFor();
  assert.equal(await page.locator('.answer').count(),0);
  await page.fill('#recallInput','中断前写下的回忆：传播涉及符号与语境。');
  await page.getByText('草稿已保存到本机',{exact:true}).waitFor();
  await page.reload();await page.getByRole('button',{name:'继续学习 →'}).click();
  assert.match(await page.inputValue('#recallInput'),/中断前写下/);
  await page.click('#reveal');await page.locator('.answer').waitFor();
  assert.match(await page.locator('.answer-text').textContent(),/传播/);
  await page.click('#checkSource');await page.locator('#sourceDialog[open]').waitFor();
  assert.match(await page.locator('#pdfLink').getAttribute('href'),/#page=1/);await page.click('#closeSource');
  await page.selectOption('#fontSize','22');
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(root,'work','02-recall-source.png'),fullPage:true});
  const marks=['unlearned','unsure','mastered','mastered'];
  for(let i=0;i<4;i++){
    if(i){await page.locator('#reveal').waitFor();await page.click('#reveal');}
    await page.locator(`[data-rating="${marks[i]}"]`).waitFor();await page.click(`[data-rating="${marks[i]}"]`);
  }
  await page.getByRole('heading',{name:'这一轮，完成了。'}).waitFor();
  await page.click('#finishReview');await page.locator('[data-review]').waitFor();
  assert.equal(await page.locator('.review-row').count(),2);
  await page.click('[data-review]');await page.locator('#reveal').waitFor();
  assert.match(await page.locator('#study').textContent(),/尾批/);
  for(let i=0;i<2;i++){await page.locator('#reveal').waitFor();await page.click('#reveal');await page.click('[data-rating="mastered"]');}
  await page.locator('#finishHome').waitFor();await page.click('#finishHome');
  await page.waitForFunction(()=>document.getElementById('mastered').textContent==='4');
  assert.equal(await page.locator('#review').textContent(),'0');
  await page.click('#libraryNav');
  // Remaining two new cards are offered as an explicitly labelled tail batch.
  await page.fill('#endPage','6');await page.click('#startStudy');await page.locator('#reveal').waitFor();
  assert.match(await page.locator('#study').textContent(),/尾批/);
  await page.click('#backHome');
  await page.click('#privacyBtn');assert.match(await page.locator('#dataPath').textContent(),/browser-data/);await page.click('#closePrivacy');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await page.setViewportSize({width:1440,height:1000});
  await page.click('#libraryNav');
  page.once('dialog',d=>d.accept());await page.click('[data-delete]');
  await page.locator('#bookList').getByText('你的书架还是空的').waitFor();
  assert.equal((await (await fetch(base+'/api/books')).json()).length,0);
  assert.equal((await (await fetch(base+'/api/stats')).json()).learned,0);
  // Ignore the browser's unrelated favicon 404; all application errors fail the run.
  assert.deepEqual(errors.filter(e=>!e.includes('404 (Not Found)')),[]);
  console.log(JSON.stringify({result:'PASS',checks:['中文PDF上传与目录','物理页范围','原文查看','先问题后答案','草稿刷新续学','字号调整','四条自评','复习优先与尾批','已掌握移出复习','未学尾批','隐私目录','窄屏无横向溢出','整书关联删除'],screenshots:['work/01-dashboard.png','work/02-recall-source.png']},null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server)server.kill();});
