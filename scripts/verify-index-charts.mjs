// Read-only Chrome verification of the generated local chart artifact.
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';import os from 'node:os';import {pathToFileURL} from 'node:url';
const base=path.resolve(process.argv[2]);const out=path.join(base,'chart-verification');await mkdir(out,{recursive:true});
const profile=await mkdtemp(path.join(os.tmpdir(),'indices-browser-'));
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-component-update','--disable-sync','--metrics-recording-only','--disable-features=MediaRouter,OptimizationHints','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:'ignore'});
const pause=ms=>new Promise(r=>setTimeout(r,ms));let ws;let seq=0;const pending=new Map(),errors=[],remote=[],results=[];
try{
 let port;for(let i=0;i<150;i++){try{port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break}catch{if(chrome.exitCode!==null)throw Error('Chrome exited');await pause(100)}}
 if(!port)throw Error('Chrome startup timeout');
 const pages=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();ws=new WebSocket(pages.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise((ok,no)=>{ws.onopen=ok;ws.onerror=no});
 ws.onmessage=e=>{const r=JSON.parse(e.data);if(r.method==='Runtime.exceptionThrown')errors.push(r.params);if(r.method==='Network.requestWillBeSent'&&/^https?:/.test(r.params.request.url))remote.push(r.params.request.url);if(r.id){const p=pending.get(r.id);if(p){pending.delete(r.id);clearTimeout(p.timer);r.error?p.no(Error(JSON.stringify(r.error))):p.ok(r.result)}}};
 const call=(method,params={})=>new Promise((ok,no)=>{const id=++seq;const timer=setTimeout(()=>{if(pending.has(id)){pending.delete(id);no(Error('CDP timeout '+method))}},30000);pending.set(id,{ok,no,timer});ws.send(JSON.stringify({id,method,params}))});
 const js=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result?.value};
 const assert=async(name,expression)=>{let pass=await js(expression);results.push({name,pass:!!pass});if(!pass)throw Error(name)};
 const choose=async(id,value)=>js(`document.getElementById(${JSON.stringify(id)}).value=${JSON.stringify(value)};document.getElementById(${JSON.stringify(id)}).dispatchEvent(new Event('change'));true`);
 await call('Runtime.enable');await call('Network.enable');await call('Network.setBlockedURLs',{urls:['http://*','https://*']});await call('Page.enable');await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1200,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:pathToFileURL(path.join(base,'charts/index.html')).href});
 for(let i=0;i<150;i++){if(await js(`document.readyState==='complete'&&!!window.INDEX_EXPLORER`))break;await pause(100)}
 await assert('Default chart has historical levels',`INDEX_EXPLORER.rows.filter(r=>r.index_close!==null).length>100`);
 for(const group of ['TYRES_REVIEWED_CORE','EMS_REVIEWED_CORE','TRANSFORMERS_REVIEWED_CORE','CARBON_BLACK','PUBLIC_BANKS']){
  await choose('group',group);await choose('frame','Daily');
  await assert('Daily lines and RS55 '+group,`INDEX_EXPLORER.series.some(s=>s.title==='RS55'&&s.series.data().some(r=>r.value!==undefined))&&INDEX_EXPLORER.series.some(s=>s.title==='index_close'&&s.series.data().filter(r=>r.value!==undefined).length>100)`);
  await choose('frame','4-hour');
  await assert('Four-hour anchor and completed source '+group,`INDEX_EXPLORER.rows.every(r=>['09:15','13:15'].includes(r.start.slice(11,16))&&Date.parse(r.end)<=Date.parse(r.available_at))`);
  await assert('Four-hour RS55 available '+group,`INDEX_EXPLORER.series.some(s=>s.title==='RS55'&&s.series.data().some(r=>r.value!==undefined))`);
 }
 await choose('group','TYRES_REVIEWED_CORE');await choose('frame','Weekly');
 await assert('Weekly benchmark requires every expected daily input',`(()=>{const key=document.getElementById('broad').value,saved=D.official[key],g=D.weekly_grid[0];D.official[key]=saved.filter(r=>r.date!==g.daily_dates[0]);const result=benchmark(key)[0]===undefined;D.official[key]=saved;return result})()`);
 await choose('company','CEATLTD');await choose('frame','Daily');await choose('sector','NSE_INDEX|Nifty Auto');
 await assert('Company selects Tyres with sector comparison',`document.getElementById('group').value==='TYRES_REVIEWED_CORE'&&INDEX_EXPLORER.series.some(s=>s.title==='Sector benchmark'&&s.series.data().length>100)`);
 await assert('Value proxy uses the labelled left axis',`INDEX_EXPLORER.series.find(s=>s.title==='₹ crore proxy').series.options().priceScaleId==='left'`);
 await choose('peers','loo');await assert('Leave-one-out excludes CEAT',`INDEX_EXPLORER.rows[0].expected_constituents===4`);
 await choose('peers','full');await choose('broad','NSE_INDEX|Nifty 50');
 await assert('Broad benchmark control works',`INDEX_EXPLORER.series.some(s=>s.title==='Broad benchmark'&&s.series.data().some(r=>r.value!==undefined))`);
 await js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');let shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(path.join(out,'subsector-explorer.png'),Buffer.from(shot.data,'base64'));
 await choose('kind','official');await choose('official','NSE_INDEX|Nifty Bank');await assert('Official NSE chart actual daily points',`INDEX_EXPLORER.rows.filter(r=>r.index_close!==null).length>200&&document.getElementById('status').textContent.includes('Nifty Bank')`);
 shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(path.join(out,'official-explorer.png'),Buffer.from(shot.data,'base64'));
 if(errors.length||remote.length)throw Error('Browser exception or remote request');
 await writeFile(path.join(out,'result.json'),JSON.stringify({results,errors,remote},null,2));console.log(JSON.stringify({passed:results.length,errors:errors.length,remote:remote.length}));
}finally{ws?.close();chrome.kill();}
