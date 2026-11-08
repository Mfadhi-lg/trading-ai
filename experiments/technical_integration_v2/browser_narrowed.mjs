// Installed Node/Chrome only. No dependency downloads or external requests.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const base=path.join(root,'artifacts/technical-integration-v2');
const output=path.join(base,'narrowed-global');
const profile=await mkdtemp(path.join(os.tmpdir(),'tech06-browser-'));
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',[
 '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
 '--disable-background-networking','--disable-component-update','--disable-sync',
 '--metrics-recording-only','--disable-features=MediaRouter,OptimizationHints',
 '--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:'ignore'});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const checks=[],errors=[],remote=[];
let ws;
try{
 let port;
 for(let i=0;i<150;i++){try{port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break}catch{if(chrome.exitCode!==null)throw Error('Chrome exited');await pause(100)}}
 if(!port)throw Error('Chrome startup timeout');
 const pages=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();
 ws=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
 let seq=0;const pending=new Map();
 ws.onmessage=e=>{const r=JSON.parse(e.data);if(r.method==='Runtime.exceptionThrown')errors.push(r.params);if(r.method==='Network.requestWillBeSent'&&/^https?:/.test(r.params.request.url))remote.push(r.params.request.url);if(r.id&&pending.has(r.id)){const {resolve,reject}=pending.get(r.id);pending.delete(r.id);r.error?reject(Error(JSON.stringify(r.error))):resolve(r.result)}};
 const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
 const js=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result?.value};
 const check=async(name,expr)=>{if(!await js(expr))throw Error(name+' '+JSON.stringify(await js('({url:location.href,selected:document.getElementById("global-recovered-context")?.dataset.selectedDate,rows:document.querySelectorAll("#global-recovered-context tr").length})')));checks.push(name)};
 const navigate=async(relative,ready)=>{const url=pathToFileURL(path.join(base,'charts',relative)).href;await call('Page.navigate',{url});for(let i=0;i<150;i++){if(await js(`location.href===${JSON.stringify(url)}&&document.readyState==='complete'&&(${ready})`))return;await pause(100)}throw Error('page timeout '+relative)};
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
 await call('Network.setBlockedURLs',{urls:['http://*','https://*']});
 await call('Emulation.setDeviceMetricsOverride',{width:1520,height:1200,deviceScaleFactor:1,mobile:false});
 await navigate('global-market.html','Boolean(window.GLOBAL_REVIEW?.charts.quote)');
 await check('13 individually reported components','TECH06_GLOBAL.status.length===13&&document.querySelectorAll("#components tr").length===14');
 await check('Retained Dow included without relabeling Tech100','TECH06_GLOBAL.status.find(r=>r.series==="DJIA").accepted_daily_observations===227&&TECH06_GLOBAL.status.find(r=>r.series==="US_TECH100").label.startsWith("US Tech 100")');
 await check('Verified and retrospective values remain separate','Object.values(TECH06_GLOBAL.context).flat().every(r=>r.verified_value===null&&!r.decision_time_verified)');
 await check('Original accepted bars preserved and GIFT integrated','TECH06_GLOBAL_BARS.filter(r=>r.unit==="days"&&!["DJIA","GIFT_NIFTY"].includes(r.series)).length===990&&TECH06_GLOBAL_BARS.filter(r=>r.unit==="minutes"&&r.series!=="GIFT_NIFTY").length===41238&&TECH06_GLOBAL_BARS.filter(r=>r.unit==="minutes"&&r.series==="GIFT_NIFTY").length===13216');
 await check('GIFT status remains generic and invalid daily quote excluded','TECH06_GLOBAL.status.find(r=>r.series==="GIFT_NIFTY").status==="PARTIAL"&&!TECH06_GLOBAL_BARS.some(r=>r.series==="GIFT_NIFTY"&&r.unit==="days"&&r.date==="2026-07-19")');
 await check('Invalid WTI daily bar absent','!TECH06_GLOBAL_BARS.some(r=>r.series==="WTI"&&r.unit==="days"&&r.date==="2026-06-18")');
 await check('237 Treasury dates and independently labeled spread','TECH06_GLOBAL.treasury.length===237&&TECH06_GLOBAL.treasury.at(-1).spread_10y_2y_bp===38&&document.body.textContent.includes("basis points")');
 await js('GLOBAL_REVIEW.inspect("2026-03-10","PREOPEN")');
 await check('Preopen checkpoint inspection renders 18 source channels','GLOBAL_REVIEW.selectedDate==="2026-03-10"&&GLOBAL_REVIEW.selectedPhase==="PREOPEN"&&document.querySelectorAll("#checkpoint tr").length===19');
 await js('document.getElementById("phase").value="POSTCLOSE";document.getElementById("phase").dispatchEvent(new Event("change"))');
 await check('Postclose selector uses 18:00 IST','GLOBAL_REVIEW.selectedPhase==="POSTCLOSE"&&document.getElementById("cutoff-description").textContent.includes("18:00")');
 await js('GLOBAL_REVIEW.inspect("2026-08-11","PREOPEN")');
 await check('Unrequested August minute windows remain absent','TECH06_GLOBAL.context["2026-08-11"].filter(r=>r.channel==="SAMPLED_MINUTE").every(r=>r.retrospective_value===null)');
 for(const key of ['USDINR','BRENT','WTI','US_TECH100','DJIA','GIFT_NIFTY']){
  await js(`document.getElementById('series').value=${JSON.stringify(key)};GLOBAL_REVIEW.windows()`);
  await check('Daily plot '+key,`GLOBAL_REVIEW.activeBars.length>0&&GLOBAL_REVIEW.activeBars.every(r=>r.series===${JSON.stringify(key)}&&r.unit==='days')`);
  if(key!=='DJIA'){
   for(const number of await js('[...document.getElementById("window").options].slice(1).map(r=>r.value)')){
    await js(`document.getElementById('window').value=${JSON.stringify(number)};GLOBAL_REVIEW.plot()`);
    await check('Sample minute plot '+key+'/'+number,'GLOBAL_REVIEW.activeBars.length>0&&GLOBAL_REVIEW.activeBars.every(r=>r.unit==="minutes")');
   }
  }
 }
 await js('document.getElementById("series").value="US_TECH100";GLOBAL_REVIEW.windows();GLOBAL_REVIEW.inspect("2026-06-19","PREOPEN")');
 await js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
 const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
 await writeFile(path.join(output,'global-market-preview.png'),Buffer.from(shot.data,'base64'));
 const preservation=JSON.parse(await readFile(path.join(output,'INTEGRATION_PRESERVATION.json'),'utf8'));
 for(const r of preservation.charts){
  const relative=path.relative(path.join(base,'charts'),path.join(root,r.chart));
  await navigate(relative,'Boolean(window.ER03C?.state.ready)');
  await check('Original technical chart initialized '+relative,'ER03C.state.ready===true');
  await js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date==="2026-06-19"))');
  await check('Recovered global context synchronized '+relative,'document.getElementById("global-recovered-context").dataset.selectedDate==="2026-06-19"&&document.querySelectorAll("#global-recovered-context tr").length===37');
  await check('Treasury and GIFT quotation visible '+relative,'document.getElementById("global-recovered-context").textContent.includes("Treasury 10Y")&&document.getElementById("global-recovered-context").textContent.includes("GIFT Nifty")&&!document.getElementById("global-recovered-context").textContent.includes("PENDING_SEPARATE_FOUR_REQUEST_GIFT_AUTHORIZATION")');
 }
 if(errors.length||remote.length)throw Error('Browser errors or remote page requests');
 checks.push('No JavaScript exceptions or remote page requests');
 await writeFile(path.join(output,'BROWSER_VALIDATION.json'),JSON.stringify({passed:true,assertions:checks.length,chartsTested:preservation.charts.length+1,checks,javascriptErrors:errors,remoteRequests:remote},null,2)+'\n');
 console.log(JSON.stringify({passed:true,assertions:checks.length,chartsTested:preservation.charts.length+1}));
}catch(error){
 await writeFile(path.join(output,'BROWSER_VALIDATION.json'),JSON.stringify({passed:false,checks,error:String(error),javascriptErrors:errors,remoteRequests:remote},null,2)+'\n');
 throw error;
}finally{
 if(ws)ws.close();chrome.kill('SIGTERM');
 await new Promise(r=>chrome.exitCode!==null?r():chrome.once('exit',r));
 await rm(profile,{recursive:true,force:true,maxRetries:3,retryDelay:100});
}
