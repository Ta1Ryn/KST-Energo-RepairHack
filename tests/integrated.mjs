import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const config=JSON.parse(await fs.readFile(path.join(root,'BackendFork/postgres.local.json')));
const name='fork_test_'+randomUUID().replaceAll('-','');
const connection=config.Database.ConnectionString.replace('Database=fork_demo','Database='+name);
assert.ok(connection.includes('Database='+name));
const socket=path.join(root,'.postgres/socket'),user=process.env.USER;
const pg=['-h',socket,'-p','5433','-U',user];
execFileSync('createdb',[...pg,name]);
const artifacts=path.join(root,'.integration','test-'+name);await fs.mkdir(artifacts);
const probe=createServer().listen(0,'127.0.0.1');await once(probe,'listening');
const url='http://127.0.0.1:'+probe.address().port;await new Promise(resolve=>probe.close(resolve));
let processHandle,token='',checks=0;
async function launch(){
 const log=(await import('node:fs')).openSync(path.join(artifacts,'api.log'),'a');
 processHandle=spawn('dotnet',[path.join(root,'BackendFork/bin/Debug/net10.0/BackendFork.dll')],{
  cwd:path.join(root,'BackendFork'),stdio:['ignore',log,log],env:{...process.env,ASPNETCORE_URLS:url,
   Workforce__Enabled:'true',Database__Provider:'PostgreSql',Database__ConnectionString:connection,
   Database__ImportExistingJson:'false',Backend__SeedDemoData:'true',Backend__DataDirectory:artifacts,
   Ai__BaseUrl:'http://127.0.0.1:8001',Ai__TimeoutSeconds:'120'}});
 (await import('node:fs')).closeSync(log);
 for(let i=0;i<120;i++){
  if(processHandle.exitCode!==null)throw Error('API exited; see '+artifacts+'/api.log');
  try{if((await fetch(url+'/health')).ok)return}catch{}
  await new Promise(resolve=>setTimeout(resolve,250));
 }
 throw Error('API readiness timeout');
}
async function stop(){if(processHandle?.exitCode===null){const done=once(processHandle,'exit');processHandle.kill();await done}}
async function call(method,route,body,expected=200,auth=token){
 const headers=auth?{Authorization:'Bearer '+auth}:{};let payload=body;
 if(body!==undefined && !(body instanceof FormData)){headers['Content-Type']='application/json';payload=JSON.stringify(body)}
 let response=await fetch(url+route,{method,headers,body:payload});
 for(let retry=0;response.status===429 && expected!==429 && retry<5;retry++){await response.arrayBuffer();await new Promise(resolve=>setTimeout(resolve,15000));response=await fetch(url+route,{method,headers,body:payload})}
 const text=await response.text();assert.equal(response.status,expected,method+' '+route+': '+text);
 checks++;return text?JSON.parse(text):null;
}
const mark=message=>console.log('PASS '+message);
try{
 const cv=await(await fetch('http://127.0.0.1:8001/health')).json();assert.match(cv.modelVersion,/^repairhack-cv-v[12]-[a-f0-9]{12}$/);
 await launch();
 assert.equal((await call('GET','/api/workforce/status',undefined,200,'')).schemaVersion,7);
 token=(await call('POST','/api/workforce/bootstrap',{employeeNumber:'TEST-ADMIN',fullName:'Synthetic administrator',pin:'4242'},200,'')).token;
 const admin=token;
 const all=await call('GET','/api/equipment');assert.ok(all.total>0);
 const eid=all.items[0].id;
 const card=await call('GET','/api/equipment/'+eid);
 assert.ok(!('qrCode' in card));
 assert.equal((await call('GET','/api/equipment?search='+encodeURIComponent(card.serialNumber))).items[0].id,eid);
 const component=card.components[0],base='/api/equipment/'+eid+'/components/'+component.id;
 const facility=await call('POST','/api/admin/facilities',{code:'TEST-ENTERPRISE',name:'Synthetic enterprise',type:'enterprise'});
 const workshop=await call('POST','/api/admin/facilities',{code:'TEST-WORKSHOP',name:'Synthetic workshop',parentId:facility.id});
 const department=await call('POST','/api/admin/departments',{code:'TEST-DEPT',name:'Synthetic department'});
 await call('POST','/api/admin/department-facilities',{departmentId:department.id,facilityId:workshop.id,reason:'Synthetic test'},204);
 await call('PUT','/api/admin/equipment/'+eid+'/facility',{facilityId:workshop.id,reason:'Synthetic placement'},204);
 const employee=await call('POST','/api/admin/employees',{fullName:'Synthetic worker',pin:'1357',departmentId:department.id});
 await call('POST','/api/admin/employees',{fullName:'Duplicate PIN',pin:'1357'},409);
 const neighbor=await call('POST','/api/admin/employees',{employeeNumber:'TEST-NEIGHBOR',fullName:'Synthetic neighbor',pin:'9876'});
 const grant=await call('POST','/api/admin/access',{employeeId:employee.id,equipmentId:eid,includeDescendants:false,reason:'Synthetic test grant'});
 mark('PostgreSQL v7, bootstrap, nameplate identity, departments, object hierarchy and equipment assignment');
 let worker=await call('POST','/api/workforce/login',{pin:'1357'},200,'');
 assert.equal(worker.mustChangePin,true);token=worker.token;
 await call('GET','/api/equipment',undefined,403);
 worker=await call('POST','/api/workforce/pin',{currentPin:'1357',newPin:'2468'});token=worker.token;
 assert.equal((await call('GET','/api/equipment')).total,1);
 await call('POST','/api/workforce/pin',{currentPin:'2468',newPin:'4242'},409);
 await call('POST','/api/admin/employees',{employeeNumber:'NO',fullName:'No',pin:'1111'},403);
 mark('Employee login, mandatory PIN change and limited equipment access');
 const image=await fs.readFile(path.join(root,'ComputerVision/functions/defect_photo_contact_sheet.jpg'));
 const counts=()=>execFileSync('psql',[...pg,'-d',name,'-Atc','SELECT (SELECT count(*) FROM storage.photos), (SELECT count(*) FROM ai.analyses)'],{encoding:'utf8'}).trim();
 const beforeLive=counts();
 const liveImage=await fs.readFile(path.join(root,'ComputerVision/datasets/case-v1/images/da40c8b22b8c87214d1da23e.jpg'));
 const liveForm=()=>{const f=new FormData();f.set('file',new Blob([liveImage],{type:'image/jpeg'}),'frame.jpg');return f};
 await call('POST','/api/vision/live',liveForm(),401,'');
 const live=await call('POST','/api/vision/live',liveForm());
 assert.equal(live.modelVersion,cv.modelVersion);assert.ok(Array.isArray(live.detections));
 const invalidLive=new FormData();invalidLive.set('file',new Blob(['not a photo']),'bad.jpg');
 await call('POST','/api/vision/live',invalidLive,415);
 assert.equal(counts(),beforeLive,'Live frames must not be stored as photos or analyses');
 mark('Authenticated live CV preview; camera frames leave no photos or analyses in PostgreSQL');
 const form=new FormData();form.set('file',new Blob([image],{type:'image/jpeg'}),'test-photo.jpg');
 const uploaded=await call('POST','/api/photos',form,201);
 const analysis=await call('POST',base+'/analyses',{photoId:uploaded.id,installationId:component.current.id},201);
 assert.equal(analysis.modelVersion,cv.modelVersion);assert.ok(['possibleDefect','checkRecommended'].includes(analysis.visualStatus));
 for(const detection of analysis.detections){const b=detection.box;assert.ok(b.x>=0&&b.y>=0&&b.width>0&&b.height>0&&b.x+b.width<=1.000001&&b.y+b.height<=1.000001)}
 const history='/api/equipment/'+eid+'/telemetry-history';
 const timestamp=new Date().toISOString();
 const sensor=component.sensors[0];
 if(sensor){await call('POST',base+'/telemetry',{installationId:component.current.id,measuredAt:timestamp,sensors:[{key:sensor.key,value:80,unit:sensor.unit}]},200,admin);
 const rows=await call('GET',history);assert.ok(rows.some(r=>r.key===sensor.key&&r.value===80));}
 mark('Real selected CV inference, uploaded photo and normalized boxes: '+cv.modelVersion);
 const inspection=await call('POST','/api/equipment/'+eid+'/inspections',{performedAt:new Date().toISOString(),comment:'Synthetic end-to-end inspection',items:[{
  componentId:component.id,installationId:component.current.id,action:'checked',photoIds:[uploaded.id],analysisId:analysis.id,comment:'Synthetic photo and analysis'}]},201);
 const report='/api/equipment/'+eid+'/inspections/'+inspection.id;
 const pdf=await fetch(url+report+'/pdf',{headers:{Authorization:'Bearer '+token}});assert.equal(pdf.status,200);
 const content=Buffer.from(await pdf.arrayBuffer());assert.equal(content.subarray(0,4).toString(),'%PDF');assert.ok(content.length>1000);
 await fs.writeFile(path.join(artifacts,'inspection.pdf'),content);
 mark('Inspection includes photo and analysis; Unicode PDF generated');
 const outsider=await call('POST','/api/workforce/login',{pin:'9876'},200,'');
 const neighborSession=await call('POST','/api/workforce/pin',{currentPin:'9876',newPin:'8642'},200,outsider.token);
 assert.equal((await call('GET','/api/equipment',undefined,200,neighborSession.token)).total,0);
 await call('GET','/api/equipment/'+eid,undefined,404,neighborSession.token);
 await call('GET',report,undefined,404,neighborSession.token);
 await call('GET',history,undefined,404,neighborSession.token);
 await call('GET','/api/photos/'+uploaded.id,undefined,404,neighborSession.token);
 mark('Other employee cannot access equipment, inspection or photo');
 await stop();await launch();
 assert.equal((await call('GET',base+'/analyses')).items[0].id,analysis.id);
 assert.equal((await call('GET',report)).id,inspection.id);
 mark('Database, sessions, photos, analysis and inspection survive API restart');
 await call('POST','/api/admin/access/'+grant.id+'/revoke',{reason:'Synthetic revocation'},204,admin);
 assert.equal((await call('GET','/api/equipment')).total,0);
 await call('GET','/api/equipment/'+eid,undefined,404);
 await call('GET','/api/photos/'+uploaded.id,undefined,404);
 mark('Revocation removes access immediately');
 const audit=await call('GET','/api/admin/audit',undefined,200,admin);
 assert.ok(!JSON.stringify(audit).includes('argon2id'));
 await fs.writeFile(path.join(artifacts,'result.json'),JSON.stringify({result:'PASS',httpChecks:checks,cv:cv.modelVersion,inspectionId:inspection.id},null,2));
 console.log('SUCCESS: '+checks+' integrated HTTP checks with PostgreSQL and real CV. Artifacts: '+artifacts);
}finally{await stop();execFileSync('dropdb',[...pg,name]);}
