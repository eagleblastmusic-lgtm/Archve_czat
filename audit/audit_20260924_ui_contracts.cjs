// Reuse the existing offline DOM fixture; no browser/profile writes or HTTP.
const fs=require('node:fs'),vm=require('node:vm');
const fixture=fs.readFileSync('audit/regression_loading_frontend.cjs','utf8').split('(async()=>{')[0];
const scenario=`(async()=>{
 const records=[];
 const h=env({videos:[],currentPage:1},{videoGrid:element(),videoCount:element(),statPageVideos:element()});
 const requests=[];
 h.ArchivebateAPI={
   postJSON:async url=>{requests.push(['POST',url]);return {active_revision:8,refresh_revision:9,refresh_pending:true};},
   getJSON:async url=>{requests.push(['GET',url]);return {snapshot_id:'8',catalog_revision:8,revision:8,videos:[{id:'old'}],complete:true,catalog_complete:true,page_count:1};}
 };
 const streams=[];h.EventSource=class{constructor(url){streams.push(url);}close(){}};
 load(h,'video-views.js');await h.ArchivebateVideoViews.loadHomeVideos(1,true);
 records.push({case:'catalog_refresh_token',expected_revision:9,actual_revision:h.ArchivebateAppContext.state.catalogRevision,requests,streams});
 const video=element();let plays=0;video.play=()=>{plays++;return Promise.resolve();};
 const m=env({}, {modalVideo:video,videoModal:element(),videoLoader:element(),modalCenterPlay:element()});
 let requested='';m.ArchivebateAPI={getJSON:async url=>{requested=url;throw Object.assign(new Error('405 force refresh requires POST'),{status:405});},postJSON:async()=>({})};
 load(m,'video-modal.js');await m.ArchivebateVideoModal.open({id:'fixture',username:'Fixture'},{forceRefresh:true});
 records.push({case:'modal_retry_method',requested,plays,expected_method:'POST'});
 fs.writeFileSync('audit/2026-09-24/ui_contract_counterexamples.json',JSON.stringify(records,null,2));
 console.log(JSON.stringify(records,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;});`;
vm.runInNewContext(fixture+scenario,{require,console,URL,performance,AbortController,setTimeout,clearTimeout,process});
