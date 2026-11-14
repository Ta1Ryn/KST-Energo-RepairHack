import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const project=path.join(root,'BackendFork');
const data=path.join(root,'smoke-artifacts','run-'+Date.now());
await fs.mkdir(data,{recursive:true});
const pgConnection=process.env.FORK_TEST_POSTGRES_CONNECTION;
if(pgConnection&&!/(?:Database|Initial Catalog)\s*=\s*fork_test_[a-f0-9]{32}\s*(?:;|$)/i.test(pgConnection))throw new Error('PostgreSQL tests require an isolated fork_test_<guid> database');
let serverProcess,logs='',token='',aiMode='valid',aiObserved=false;
let aiStarted,releaseAi;
const aiServer=createServer(async(req,res)=>{
 let request='';for await(const chunk of req)request+=chunk.toString('latin1');
 aiObserved=['file','componentId','installationId'].every(name=>new RegExp('name="?'+name+'"?').test(request));
 if(aiMode==='pending'){aiStarted();await new Promise(resolve=>releaseAi=resolve)}
 res.setHeader('Content-Type','application/json');
 if(aiMode==='missing'){res.end('{}');return}
 if(aiMode==='error'){res.statusCode=500;res.end('{}');return}
 const body={visualStatus:'possibleDefect',summary:'Possible corrosion',recommendation:'Inspect component',
  modelVersion:'test-stub-only',confidence:.9,detections:[{label:'corrosion',confidence:.9,box:{x:.1,y:.2,width:.3,height:.4}}]};
 if(aiMode==='box')body.detections[0].box.width=2;
 res.end(JSON.stringify(body));
});
aiServer.listen(0,'127.0.0.1');await once(aiServer,'listening');
const aiPort=aiServer.address().port;
// Ask the OS for a free API port. The listener is released before starting Kestrel.
const probe=createServer();probe.listen(0,'127.0.0.1');await once(probe,'listening');
const apiPort=probe.address().port;await new Promise(resolve=>probe.close(resolve));
const url='http://127.0.0.1:'+apiPort;
async function launch(){
 logs='';
 serverProcess=spawn(process.platform==='win32'?path.join(process.env.LOCALAPPDATA,'Microsoft','dotnet','dotnet.exe'):'dotnet',[path.join(project,'bin','Debug','net10.0','BackendFork.dll')],{
  cwd:project,windowsHide:true,stdio:'inherit',env:{...process.env,ASPNETCORE_ENVIRONMENT:'Development',
   ASPNETCORE_URLS:url,Database__Provider:pgConnection?'PostgreSql':'Json',Database__ConnectionString:pgConnection||'',Database__ImportExistingJson:'false',Backend__DataDirectory:data,Backend__SeedDemoData:'true',
   Workforce__Enabled:'false',Logging__LogLevel__Default:'Error',Ai__BaseUrl:'http://127.0.0.1:'+aiPort,Ai__TimeoutSeconds:'2',DOTNET_CLI_TELEMETRY_OPTOUT:'1'}
 });

 for(let attempt=0;attempt<100;attempt++){
  if(serverProcess.exitCode!==null)throw new Error('API exited: '+logs);
  try{const response=await fetch(url+'/health');if(response.ok)return}catch{}
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 throw new Error('API startup timed out: '+logs);
}
async function stop(){
 if(serverProcess&&serverProcess.exitCode===null){const exited=once(serverProcess,'exit');serverProcess.kill();await exited}
}
let checks=0;
async function call(method,endpoint,body,expected=200,withAuth=true){
 const headers=withAuth&&token?{Authorization:'Bearer '+token}:{};
 const options={method,headers};
 if(body instanceof FormData)options.body=body;
 else if(body!==undefined){headers['Content-Type']='application/json';options.body=JSON.stringify(body)}
 const response=await fetch(url+endpoint,options);
 const text=await response.text();let result;try{result=JSON.parse(text)}catch{result=text}
 assert.equal(response.status,expected,method+' '+endpoint+': '+text);
 checks++;return result;
}
function mark(label){console.log('PASS '+label)}
const eid='10000000-0000-0000-0000-000000000001',cid='20000000-0000-0000-0000-000000000001',
 other='20000000-0000-0000-0000-000000000002',iid='30000000-0000-0000-0000-000000000001';
const component='/api/equipment/'+eid+'/components/'+cid;
const now=()=>new Date().toISOString();
const pin='642891';
try{
 await launch();
 await call('GET','/health');
 const docs=await call('GET','/docs.html');assert.match(docs,/BackendFork API/);
 const spec=await call('GET','/openapi/v1.json');
 await fs.writeFile(path.join(data,'openapi.json'),JSON.stringify(spec,null,2));
 assert.equal(spec.openapi,'3.0.3');
 assert.ok(spec.paths['/api/equipment/{equipmentId}/components/{componentId}/telemetry']);
 assert.ok(spec.paths['/api/photos'].post.requestBody.content['multipart/form-data'].schema.properties.file);
 assert.equal(spec.paths['/api/auth/logout'].post.responses['204'].description,'HTTP 204');
 await call('GET','/api/equipment',undefined,401,false);
 const configured=await call('GET','/api/auth/status');assert.equal(configured.configured,false);
 const session=await call('POST','/api/auth/setup',{displayName:'Test engineer',pin});
 token=session.token;
 await call('POST','/api/auth/setup',{displayName:'Other',pin},409);
 await call('POST','/api/auth/login',{pin:'1111'},401);
 token=(await call('POST','/api/auth/login',{pin})).token;
 assert.equal((await call('GET','/api/auth/me')).displayName,'Test engineer');
 if(!pgConnection){
 const storage=JSON.parse(await fs.readFile(path.join(data,'database.json'),'utf8'));
 assert.notEqual(storage.Account?.Hash,pin);assert.ok(storage.account.hash);assert.ok(!JSON.stringify(storage).includes(pin));
 }
 mark('PIN setup/login, authorization and hashed storage');

 assert.equal((await call('GET','/api/equipment')).total,1);
 const card=await call('GET','/api/equipment/'+eid);
 assert.ok(!('qrCode' in card));
 assert.ok(!spec.paths['/api/equipment/by-qr']);
 assert.equal(card.id,eid);
 const bearing=await call('GET',component);
 assert.equal(bearing.resource.remainingHours,300);
 assert.equal(bearing.resource.lifetimeStatus,'attention');
 assert.equal(bearing.resource.serviceStatus,'exhausted');
 assert.equal(bearing.sensors[0].status,'outOfRange');
 const seal=await call('GET','/api/equipment/'+eid+'/components/'+other);
 assert.equal(seal.resource.lifetimeStatus,'unknown');
 assert.ok(seal.resource.limitations.length>0);
 await call('GET','/api/equipment?limit=1000',undefined,400);
 await call('GET','/api/equipment/by-qr?code=retired',undefined,404);
 await call('POST','/api/equipment',{name:'Invalid'},400);
 await call('POST','/api/equipment',{name:'Broken',model:'TEST',serialNumber:'NULL',components:[null]},400);
 const creation={name:'Test motor',model:'TEST',serialNumber:'T-1',
  characteristics:{power:'1 kW'},components:[{name:'Rotor',type:'rotor',installedAt:new Date(Date.now()-3600000).toISOString(),
   normLifetimeHours:1000,serviceIntervalHours:100,operatingHours:10,hoursMeasuredAt:now(),
   sensors:[{key:'rpm',name:'RPM',unit:'rpm',min:0,max:100}]}]};
 const created=await call('POST','/api/equipment',creation,201);
 assert.equal(created.components[0].resource.remainingHours,990);
 const added=await call('POST','/api/equipment/'+created.id+'/components',{name:'Pressure',type:'pump',installedAt:new Date(Date.now()-3600000).toISOString(),sensors:[{key:'pressure',name:'Pressure',unit:'bar',min:0,max:10}]},201);
 const pressurePath='/api/equipment/'+created.id+'/components/'+added.id;
 const pressure=await call('POST',pressurePath+'/telemetry',{installationId:added.current.id,measuredAt:now(),sensors:[{key:'pressure',unit:'bar',value:12}]});
 assert.equal(pressure.sensors[0].status,'outOfRange');
 assert.equal((await call('GET','/api/equipment/'+created.id)).components.length,2);
 await call('POST','/api/equipment/'+created.id+'/components',{name:'Invalid',type:'pump',installedAt:new Date(Date.now()+3600000).toISOString(),sensors:[]},400);

 await call('POST','/api/equipment',creation,409);
 assert.equal((await call('GET','/api/equipment?search=Test%20motor')).total,1);
 mark('Nameplate identity, resource calculation, unknown data and request validation');

 await call('POST',component+'/telemetry',{installationId:iid,measuredAt:now(),operatingHours:9600,sensors:[]},409);
 await call('POST',component+'/telemetry',{installationId:iid,measuredAt:now(),operatingHours:9800,sensors:[{key:'missing',value:9}]},400);
 assert.equal((await call('GET',component)).current.operatingHours,9700);
 const measuredAt=now();
 const fresh=await call('POST',component+'/telemetry',{installationId:iid,measuredAt,operatingHours:9800,sensors:[{key:'temperature',value:40}]});
 assert.equal(fresh.resource.remainingHours,200);assert.equal(fresh.sensors[0].status,'normal');
 await call('POST',component+'/telemetry',{installationId:iid,measuredAt,operatingHours:9900,sensors:[]},409);
 await call('POST',component+'/telemetry',{installationId:iid,measuredAt:new Date(Date.now()-3600000).toISOString(),sensors:[{key:'temperature',value:80}]},409);
 await call('POST',component+'/telemetry',{installationId:iid,measuredAt:now(),sensors:null},400);
 await call('POST',component+'/telemetry',{installationId:iid,measuredAt:now(),sensors:[null]},400);
 mark('Telemetry monotonicity, timestamps and atomic rollback');

 const invalidForm=new FormData();invalidForm.append('file',new Blob(['not an image']), 'fake.jpg');
 await call('POST','/api/photos',invalidForm,415);
 const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=','base64');
 const form=new FormData();form.append('file',new Blob([png],{type:'image/png'}),'sample.png');
 const photo=await call('POST','/api/photos',form,201);
 const downloaded=await fetch(url+photo.url,{headers:{Authorization:'Bearer '+token}});
 assert.equal(downloaded.status,200);assert.deepEqual(Buffer.from(await downloaded.arrayBuffer()),png);checks++;
 await call('GET',photo.url,undefined,401,false);
 const analysis=await call('POST',component+'/analyses',{installationId:iid,photoId:photo.id},201);
 assert.equal(analysis.visualStatus,'possibleDefect');assert.ok(aiObserved);assert.equal(analysis.installationId,iid);
 aiMode='box';await call('POST',component+'/analyses',{installationId:iid,photoId:photo.id},502);
 aiMode='missing';await call('POST',component+'/analyses',{installationId:iid,photoId:photo.id},502);
 aiMode='error';await call('POST',component+'/analyses',{installationId:iid,photoId:photo.id},502);
 aiMode='pending';aiStarted=()=>{};
 const timedOut=call('POST',component+'/analyses',{installationId:iid,photoId:photo.id},504);
 await timedOut;releaseAi();releaseAi=null;
 aiMode='valid';
 assert.equal((await call('GET',component+'/analyses')).total,1);
 mark('Protected photos and local AI contract, bounding-box validation');

 const inspection='/api/equipment/'+eid+'/inspections';
 const save=async(action,extra={})=>call('POST',inspection,{performedAt:now(),authorId:'forged',
  items:[{componentId:cid,installationId:iid,action,...extra}]},201);
 const docForm=new FormData();docForm.append('file',new Blob([png],{type:'image/png'}),'instruction.png');
 docForm.append('category','instruction');docForm.append('equipmentId',eid);
 const document=await call('POST','/api/documents',docForm,201);
 assert.equal(document.category,'instruction');assert.equal(document.sha256.length,64);
 assert.equal((await call('GET','/api/documents?equipmentId='+eid)).length,1);
 await call('GET',document.url,undefined,401,false);
 const docRead=await fetch(url+document.url,{headers:{Authorization:'Bearer '+token}});
 assert.equal(docRead.status,200);assert.deepEqual(Buffer.from(await docRead.arrayBuffer()),png);checks++;
 const passport={nameplate:{manufacturer:'Test',model:'DEMO-PUMP',serialNumber:'DEMO-001',manufactureYear:2024,documentId:document.id},
  passport:{purpose:'Test',location:'Lab',operatingMode:'test',parameters:{power:'demo'},documentIds:[document.id]}};
 assert.equal((await call('PUT','/api/equipment/'+eid+'/passport',passport)).passport.location,'Lab');
 await call('PUT','/api/equipment/'+eid+'/passport',{...passport,nameplate:{serialNumber:'WRONG'}},400);
 assert.equal((await call('GET','/api/defect-types')).length,4);
 const normRequest={name:'Test limit',sensorKind:'temperature',unit:'°C',min:0,max:35,warningMax:30,
  equipmentModel:'DEMO-PUMP',componentType:'bearing',isDemo:true,sourceDocumentId:document.id};
 const norm=await call('POST','/api/standards/sensor-norms',normRequest);
 await call('POST','/api/standards/sensor-norms',{...normRequest,warningMax:50},400);
 const definition={name:'Bearing',type:'bearing',normLifetimeHours:10000,serviceIntervalHours:1000,
  sensors:[{key:'temperature',kind:'temperature',name:'Temperature',unit:'°C',normId:norm.id}]};
 await call('PUT',component+'/definition',definition);
 await call('PUT',component+'/definition',{...definition,sensors:[{...definition.sensors[0],unit:'bar'}]},400);
 const instruction=await call('POST','/api/instructions',{title:'Test manual',text:'Check sensor',documentId:document.id,isDemo:true});
 await call('POST','/api/recommendation-rules',{trigger:'sensorOutOfRange',equipmentModel:'DEMO-PUMP',sensorKind:'temperature',
  instructionId:instruction.id,message:'Inspect based on test instruction',enabled:true});
 const assessed=await call('GET',component);
 assert.equal(assessed.sensors[0].status,'outOfRange');assert.equal(assessed.sensors[0].normId,norm.id);
 assert.ok(assessed.recommendations.some(r=>r.instructionId===instruction.id&&r.isDemo));
 mark('Documents, nameplates/passports, separate norms and instruction-based recommendations');
 const work=await save('serviced');
 assert.equal(work.equipmentSnapshot.components[0].sensors[0].max,35);
 await call('PUT','/api/standards/sensor-norms/'+norm.id,{...normRequest,max:90,warningMax:75});
 assert.equal((await call('GET',component)).sensors[0].normRevision,2);
 const oldReport=await call('GET',inspection+'/'+work.id);
 assert.equal(oldReport.equipmentSnapshot.components[0].sensors[0].max,35);
 const pdfResponse=await fetch(url+inspection+'/'+work.id+'/pdf',{headers:{Authorization:'Bearer '+token}});
 assert.equal(pdfResponse.status,200);assert.equal(pdfResponse.headers.get('content-type'),'application/pdf');
 const pdf=Buffer.from(await pdfResponse.arrayBuffer());assert.ok(pdf.subarray(0,8).toString().startsWith('%PDF-1.7'));
 const pdfText=pdf.toString('latin1');assert.ok(pdfText.includes('/ToUnicode'));assert.ok(pdfText.includes('/FontFile2'));
 const xref=Number(/startxref\s+(\d+)\s+%%EOF/.exec(pdfText)[1]);assert.equal(pdf.subarray(xref,xref+4).toString(),'xref');
 await fs.writeFile(path.join(data,'report.pdf'),pdf);checks++;
 await call('GET',inspection+'/'+work.id+'/pdf',undefined,401,false);
 mark('PDF with Unicode font and immutable report snapshot');
 assert.equal(work.authorId,'engineer');
 let after=await call('GET',component);assert.equal(after.resource.hoursUntilService,1000);
 await save('defectRecorded',{comment:'Leak found',photoIds:[photo.id],analysisId:analysis.id});
 await save('checked');
 assert.ok((await call('GET',component)).warnings.some(w=>w.includes('Leak found')));
 await save('checked',{defectResolved:true});
 assert.ok(!(await call('GET',component)).warnings.some(w=>w.includes('Leak found')));
 await call('POST',inspection,{performedAt:now(),items:[
  {componentId:cid,installationId:iid,action:'replaced',replacement:{serialNumber:'NEW',initialOperatingHours:0}},
  {componentId:other,installationId:'00000000-0000-0000-0000-000000000000',action:'checked'}]},409);
 assert.equal((await call('GET',component)).current.id,iid);
 mark('Inspection author, service baseline, explicit defect resolution and transactional work');

 // AI may finish after a component is replaced. The stale result must not be stored.
 const started=new Promise(resolve=>aiStarted=resolve);aiMode='pending';
 const pending=call('POST',component+'/analyses',{installationId:iid,photoId:photo.id},409);
 await started;
 const replacement=await save('replaced',{replacement:{serialNumber:'NEW',initialOperatingHours:0}});
 releaseAi();await pending;aiMode='valid';
 after=await call('GET',component);
 assert.notEqual(after.current.id,iid);assert.equal(after.current.operatingHours,0);
 assert.equal(after.previousInstallations.length,1);
 assert.equal(after.previousInstallations[0].operatingHours,9800);
 assert.equal(after.latestVisualAnalysis,null);assert.equal(after.sensors[0].value,null);
 assert.equal(replacement.items[0].newInstallationId,after.current.id);
 await call('POST',component+'/telemetry',{installationId:iid,measuredAt:now(),operatingHours:1,sensors:[]},409);
 const history=await call('GET',inspection);assert.equal(history.total,5);
 assert.equal((await call('GET',component+'/analyses')).total,1);
 mark('Replacement history, reset counters and concurrent AI result rejection');

 await call('POST','/api/auth/logout',{},204);
 await call('GET','/api/equipment',undefined,401);
 // PIN endpoint rate limiting: only five requests per IP per minute.
 await call('POST','/api/auth/login',{pin:'1111'},401);
 await call('POST','/api/auth/login',{pin},429);
 await stop();await launch();
 token=(await call('POST','/api/auth/login',{pin})).token;
 const restored=await call('GET',component);
 assert.equal(restored.current.id,after.current.id);
 assert.equal((await call('GET',inspection)).total,5);
 mark('Logout, rate limiting and persistence after restart');
 console.log('SUCCESS: '+checks+' HTTP checks. Database: '+(pgConnection?'PostgreSQL':'JSON')+'. AI used a test stub, not YOLO.');
 console.log('Isolated test artifacts: '+data);
}catch(error){
 console.error(error);console.error(logs);process.exitCode=1;
}finally{
 if(releaseAi)releaseAi();
 await stop();await new Promise(resolve=>aiServer.close(resolve));
}
