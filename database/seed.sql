-- BackendFork demonstration dataset. Values are fictional, NOT production limits.
BEGIN;
SELECT pg_advisory_xact_lock(74102851);
INSERT INTO catalog.equipment(id,qr_code,serial_number,model,name,payload) VALUES
('10000000-0000-0000-0000-000000000001','FORK:DEMO-001','DEMO-001','DEMO-PUMP','Демонстрационный насос',
'{"id":"10000000-0000-0000-0000-000000000001","name":"Демонстрационный насос","model":"DEMO-PUMP","serialNumber":"DEMO-001","qrCode":"FORK:DEMO-001","characteristics":{"Назначение":"Тестовые данные"},"components":[]}'::jsonb),
('10000000-0000-0000-0000-000000000002','FORK:DEMO-002','DEMO-002','DEMO-MOTOR','Демонстрационный двигатель',
'{"id":"10000000-0000-0000-0000-000000000002","name":"Демонстрационный двигатель","model":"DEMO-MOTOR","serialNumber":"DEMO-002","qrCode":"FORK:DEMO-002","characteristics":{"Назначение":"Тестовые данные"},"components":[]}'::jsonb)
ON CONFLICT(id) DO NOTHING;
INSERT INTO catalog.nameplates(equipment_id,payload) VALUES
('10000000-0000-0000-0000-000000000001','{"manufacturer":"DEMO","model":"DEMO-PUMP","serialNumber":"DEMO-001","manufactureYear":2024,"marking":"TEST SAMPLE"}'),
('10000000-0000-0000-0000-000000000002','{"manufacturer":"DEMO","model":"DEMO-MOTOR","serialNumber":"DEMO-002","manufactureYear":2024,"marking":"TEST SAMPLE"}')
ON CONFLICT(equipment_id) DO NOTHING;
INSERT INTO catalog.passports(equipment_id,payload) VALUES
('10000000-0000-0000-0000-000000000001','{"purpose":"Демонстрация осмотра насоса","location":"Тестовый стенд","operatingMode":"test","parameters":{"Мощность":"Тестовая характеристика"},"documentIds":[]}'),
('10000000-0000-0000-0000-000000000002','{"purpose":"Демонстрация осмотра двигателя","location":"Тестовый стенд","operatingMode":"test","parameters":{},"documentIds":[]}')
ON CONFLICT(equipment_id) DO NOTHING;
INSERT INTO catalog.components(id,equipment_id,name,component_type,payload) VALUES
('20000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','Подшипник','bearing','{"id":"20000000-0000-0000-0000-000000000001","name":"Подшипник","type":"bearing","sensors":[],"previousInstallations":[]}'),
('20000000-0000-0000-0000-000000000002','10000000-0000-0000-0000-000000000001','Уплотнение','seal','{"id":"20000000-0000-0000-0000-000000000002","name":"Уплотнение","type":"seal","sensors":[],"previousInstallations":[]}'),
('20000000-0000-0000-0000-000000000003','10000000-0000-0000-0000-000000000002','Ротор','rotor','{"id":"20000000-0000-0000-0000-000000000003","name":"Ротор","type":"rotor","sensors":[],"previousInstallations":[]}')
ON CONFLICT(id) DO NOTHING;
INSERT INTO catalog.installations(id,component_id,installed_at,payload) VALUES
('30000000-0000-0000-0000-000000000001','20000000-0000-0000-0000-000000000001',now()-interval '1 year',
jsonb_build_object('id','30000000-0000-0000-0000-000000000001','installedAt',now()-interval '1 year','lastServiceAt',now()-interval '40 days','hoursAtLastService',8600)),
('30000000-0000-0000-0000-000000000002','20000000-0000-0000-0000-000000000002',now()-interval '2 months',
jsonb_build_object('id','30000000-0000-0000-0000-000000000002','installedAt',now()-interval '2 months')),
('30000000-0000-0000-0000-000000000003','20000000-0000-0000-0000-000000000003',now()-interval '3 months',
jsonb_build_object('id','30000000-0000-0000-0000-000000000003','installedAt',now()-interval '3 months'))
ON CONFLICT(id) DO NOTHING;
INSERT INTO standards.resource_norms(component_id,lifetime_hours,service_interval_hours) VALUES
('20000000-0000-0000-0000-000000000001',10000,1000),
('20000000-0000-0000-0000-000000000002',5000,500),
('20000000-0000-0000-0000-000000000003',20000,2000)
ON CONFLICT(component_id) DO NOTHING;
INSERT INTO standards.sensor_norms(id,name,sensor_kind,unit,min_value,max_value,warning_max,revision,is_demo,payload) VALUES
('40000000-0000-0000-0000-000000000001','Тестовая норма температуры','temperature','°C',0,80,70,1,true,
'{"id":"40000000-0000-0000-0000-000000000001","name":"Тестовая норма температуры","sensorKind":"temperature","unit":"°C","min":0,"max":80,"warningMax":70,"revision":1,"isDemo":true}'),
('40000000-0000-0000-0000-000000000002','Тестовая норма виброскорости','vibration','mm/s',0,7.1,4.5,1,true,
'{"id":"40000000-0000-0000-0000-000000000002","name":"Тестовая норма виброскорости","sensorKind":"vibration","unit":"mm/s","min":0,"max":7.1,"warningMax":4.5,"revision":1,"isDemo":true}'),
('40000000-0000-0000-0000-000000000003','Тестовая норма давления','pressure','bar',1,6,5.5,1,true,
'{"id":"40000000-0000-0000-0000-000000000003","name":"Тестовая норма давления","sensorKind":"pressure","unit":"bar","min":1,"max":6,"warningMax":5.5,"revision":1,"isDemo":true}')
ON CONFLICT(id) DO NOTHING;
INSERT INTO telemetry.sensors(component_id,sensor_key,norm_id,sensor_kind,unit,payload) VALUES
('20000000-0000-0000-0000-000000000001','temperature','40000000-0000-0000-0000-000000000001','temperature','°C',
'{"key":"temperature","name":"Температура","kind":"temperature","unit":"°C","normId":"40000000-0000-0000-0000-000000000001"}'),
('20000000-0000-0000-0000-000000000001','vibration','40000000-0000-0000-0000-000000000002','vibration','mm/s',
'{"key":"vibration","name":"Виброскорость","kind":"vibration","unit":"mm/s","normId":"40000000-0000-0000-0000-000000000002"}'),
('20000000-0000-0000-0000-000000000002','pressure','40000000-0000-0000-0000-000000000003','pressure','bar',
'{"key":"pressure","name":"Давление","kind":"pressure","unit":"bar","normId":"40000000-0000-0000-0000-000000000003"}'),
('20000000-0000-0000-0000-000000000003','vibration','40000000-0000-0000-0000-000000000002','vibration','mm/s',
'{"key":"vibration","name":"Виброскорость","kind":"vibration","unit":"mm/s","normId":"40000000-0000-0000-0000-000000000002"}')
ON CONFLICT(component_id,sensor_key) DO NOTHING;
INSERT INTO telemetry.operating_hours(installation_id,measured_at,value) VALUES
('30000000-0000-0000-0000-000000000001',now(),9700),
('30000000-0000-0000-0000-000000000003',now(),2400)
ON CONFLICT DO NOTHING;
INSERT INTO telemetry.readings(component_id,sensor_key,installation_id,measured_at,value,unit) VALUES
('20000000-0000-0000-0000-000000000001','temperature','30000000-0000-0000-0000-000000000001',now(),88,'°C'),
('20000000-0000-0000-0000-000000000001','vibration','30000000-0000-0000-0000-000000000001',now(),7.5,'mm/s'),
('20000000-0000-0000-0000-000000000002','pressure','30000000-0000-0000-0000-000000000002',now(),4,'bar'),
('20000000-0000-0000-0000-000000000003','vibration','30000000-0000-0000-0000-000000000003',now(),2,'mm/s')
ON CONFLICT DO NOTHING;
INSERT INTO knowledge.instructions(id,title,revision,payload) VALUES
('50000000-0000-0000-0000-000000000001','Тестовая инструкция проверки датчиков',1,
'{"id":"50000000-0000-0000-0000-000000000001","title":"Тестовая инструкция проверки датчиков","revision":1,"isDemo":true,"text":"ТЕСТОВАЯ ИНСТРУКЦИЯ: проверить достоверность показания и провести осмотр по регламенту оборудования."}'),
('50000000-0000-0000-0000-000000000002','Тестовая инструкция визуального осмотра',1,
'{"id":"50000000-0000-0000-0000-000000000002","title":"Тестовая инструкция визуального осмотра","revision":1,"isDemo":true,"text":"ТЕСТОВАЯ ИНСТРУКЦИЯ: сфотографировать признак дефекта и зарегистрировать замечание для проверки специалистом."}')
ON CONFLICT(id) DO NOTHING;
INSERT INTO knowledge.recommendation_rules(id,trigger,instruction_id,payload) VALUES
('60000000-0000-0000-0000-000000000001','SensorOutOfRange','50000000-0000-0000-0000-000000000001',
'{"id":"60000000-0000-0000-0000-000000000001","trigger":"sensorOutOfRange","instructionId":"50000000-0000-0000-0000-000000000001","message":"Тестовая рекомендация: проверить датчик и узел.","enabled":true}'),
('60000000-0000-0000-0000-000000000002','VisualDefect','50000000-0000-0000-0000-000000000002',
'{"id":"60000000-0000-0000-0000-000000000002","trigger":"visualDefect","instructionId":"50000000-0000-0000-0000-000000000002","message":"Тестовая рекомендация: зафиксировать видимый признак и проверить узел.","enabled":true}')
ON CONFLICT(id) DO NOTHING;
COMMIT;
