#!/usr/bin/env python3
"""Build, seed, run isolated services, verify data, and report paired measurements."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import sqlite3
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT.parent

def execute(args, cwd=ROOT, env=None, **kwargs):
    return subprocess.run([str(x) for x in args], cwd=cwd, env=env, check=True, **kwargs)

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')

def source_hashes():
    result = {}
    for base in [ROOT, PROJECTS/'project-rest', PROJECTS/'project-grpc']:
        for p in sorted(base.rglob('*')):
            if not p.is_file() or any(x in p.relative_to(base).parts for x in ('bin','data','seed','seeds','results','__pycache__','.git')):
                continue
            if p.suffix in ('.go','.sql','.proto','.py','.sh') or p.name in ('go.mod','go.sum'):
                result[str(p.relative_to(PROJECTS))] = digest(p)
    return result

def build():
    (ROOT/'bin').mkdir(exist_ok=True)
    execute(['go','build','-o',ROOT/'bin/bench','.'])
    for version in ('rest','grpc'):
        target=ROOT/'bin'/version
        target.mkdir(exist_ok=True)
        for command in ('gateway','user-service','hr-service','migrate'):
            execute(['go','build','-o',target/command,'./cmd/'+command],cwd=PROJECTS/f'project-{version}')

def database_rows(path, table):
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        return [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY id')]

def prepare_seed(base_env):
    data=json.loads(execute([ROOT/'bin/bench','seed'],capture_output=True,text=True).stdout)
    seed=ROOT/'seeds'/f"users-{len(data['users'])}-leaves-{len(data['leaves'])}"
    seed.parent.mkdir(exist_ok=True)
    migration_hashes={}
    for service in ('user','hr'):
        for p in sorted((PROJECTS/'project-rest/migrations'/service).glob('*.sql')):
            other=PROJECTS/'project-grpc/migrations'/service/p.name
            if digest(p)!=digest(other):
                raise RuntimeError('REST/gRPC migrations differ: '+p.name)
            migration_hashes[service+'/'+p.name]=digest(p)
    if not seed.exists():
        staging=ROOT/('seed-building-'+str(os.getpid()))
        staging.mkdir()
        try:
            env=base_env|{'USER_DB':str(staging/'users.db'),'HR_DB':str(staging/'hr.db')}
            migration_status={}
            for service in ('user','hr'):
                execute([ROOT/'bin/rest/migrate',service,'up'],env=env)
                migration_status[service]=execute([ROOT/'bin/rest/migrate',service,'status'],env=env,capture_output=True,text=True).stdout
            save(staging/'migration-status.json',migration_status)
            with sqlite3.connect(staging/'users.db') as db:
                db.executemany('INSERT INTO users (id,name,email,address) VALUES (:id,:name,:email,:address)',data['users'])
            with sqlite3.connect(staging/'hr.db') as db:
                db.executemany('INSERT INTO leaves (id,user_id,start_date,end_date) VALUES (:id,:user_id,:start_date,:end_date)',data['leaves'])
            save(staging/'data.json',data)
            manifest={'migrations':migration_hashes,'files':{name:digest(staging/name) for name in ('users.db','hr.db')},'users':len(data['users']),'leaves':len(data['leaves']),'bytes':{name:(staging/name).stat().st_size for name in ('users.db','hr.db')},'migration_status':migration_status}
            save(staging/'manifest.json',manifest)
            staging.rename(seed)
        finally:
            if staging.exists():shutil.rmtree(staging)
    manifest=json.loads((seed/'manifest.json').read_text())
    if manifest['migrations']!=migration_hashes:raise RuntimeError('Seed migrations changed; preserve old seed and generate a new one before measuring.')
    for name,hash_value in manifest['files'].items():
        if digest(seed/name)!=hash_value:raise RuntimeError('Seed was modified: '+name)
    if database_rows(seed/'users.db','users')!=data['users'] or database_rows(seed/'hr.db','leaves')!=data['leaves']:
        raise RuntimeError('Seed contents do not match deterministic dataset')
    return seed,manifest,data

def copy_seed(seed,target,manifest):
    target.mkdir(parents=True)
    hashes={}
    for name,want in manifest['files'].items():
        shutil.copy2(seed/name,target/name)
        hashes[name]=digest(target/name)
        if hashes[name]!=want:raise RuntimeError('Copy hash mismatch')
    return hashes

def free_ports():
    sockets=[]
    try:
        for _ in range(3):
            sock=socket.socket()
            sock.bind(('127.0.0.1',0));sockets.append(sock)
        return [s.getsockname()[1] for s in sockets]
    finally:
        for s in sockets:s.close()

def check_final(dbdir,data,groups):
    result={}
    for table,file,threshold,payload in [('users','users.db',len(data['users']),'user'),('leaves','hr.db',len(data['leaves']),'leave')]:
        initial={r['id']:dict(r) for r in data[table]}
        created=[]
        for group in groups:
            for op in group:
                if op['resource']!=table:continue
                if op['action']=='create':
                    row=dict(op[payload]);row.pop('id');created.append(row)
                elif op['action']=='update':initial[op['id']]=dict(op[payload])|{'id':op['id']}
                elif op['action']=='delete':del initial[op['id']]
        rows=database_rows(dbdir/file,table)
        old=[r for r in rows if r['id']<=threshold]
        new=[{k:v for k,v in r.items() if k!='id'} for r in rows if r['id']>threshold]
        sort_key=lambda r:json.dumps(r,sort_keys=True)
        if old!=[initial[k] for k in sorted(initial)] or sorted(new,key=sort_key)!=sorted(created,key=sort_key):
            raise RuntimeError('Final content mismatch: '+table)
        if len(rows)!=threshold:raise RuntimeError('Final count mismatch: '+table)
        result[table]={'count':len(rows),'contents_verified':True}
    return result

def run_services(version,env,directory,ports,callback):
    processes=[];logs=[]
    try:
        for command in ('user-service','hr-service','gateway'):
            log=open(directory/(command+'.log'),'w');logs.append(log)
            processes.append(subprocess.Popen([str(ROOT/'bin'/version/command)],cwd=PROJECTS/f'project-{version}',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True))
        deadline=time.monotonic()+15
        while True:
            if any(p.poll() is not None for p in processes):raise RuntimeError('Service exited during startup; see logs')
            ready=True
            for port in ports:
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1):pass
                except OSError:ready=False
            if ready:break
            if time.monotonic()>deadline:raise RuntimeError('Service startup timed out')
            time.sleep(.05)
        return callback()
    finally:
        for p in processes:
            if p.poll() is None:p.send_signal(signal.SIGTERM)
        for p in processes:
            try:p.wait(timeout=7)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        for log in logs:log.close()

def describe_environment():
    cpu='unknown'
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):cpu=line.split(':',1)[1].strip();break
    except OSError:pass
    return {'platform':platform.platform(),'cpu':cpu,'logical_cpus':os.cpu_count(),
            'go':execute(['go','version'],capture_output=True,text=True).stdout.strip(),
            'python':platform.python_version(),'sqlite_seed_writer':sqlite3.sqlite_version,
            'gomaxprocs_env':os.environ.get('GOMAXPROCS','unset (Go default)'),
            'load_average_start':os.getloadavg(),'clock':'Go monotonic time.Now/time.Since'}

def report(results,folder,manifest,metadata):
    requests=metadata['requests_per_run']
    total=requests*metadata['repeats']*len(metadata['concurrency'])*2
    lines=['# REST ve gRPC benchmark sonuçları','',f"Tarih: {metadata['started_at']}",'',
           f"Her sürüm için {manifest['users']:,} kullanıcı ve {manifest['leaves']:,} izin içeren aynı seed kullanıldı. Her koşuda 10 işlem türüne {requests//10}’şer istek, toplam {requests:,} istek gönderildi. İki eşzamanlılık düzeyi, altışar tekrar ve iki sürüm: toplam {total:,} ölçülen istek; ayrıca koşu başına 20 ısınma isteği.",'',
           '## Toplu sonuçlar','',
           'Aşağıdaki süre ve RPS değerleri altı tekrarın ortancasıdır. Süre, on grubun ölçülen duvar sürelerinin toplamıdır. Gecikme sütunları da koşu bazındaki ortanca/p95 değerlerinin altı tekrar üzerindeki ortancasıdır.','',
           f'| Eşzamanlı istek | Sürüm | {requests} istek süresi (ms) | Başarılı istek/sn | Ortanca gecikme (ms) | p95 (ms) | Başarı / hata |',
           '| --- | --- | ---: | ---: | ---: | ---: | --- |']
    comparisons=[]
    for concurrency in (1,10):
        med={}
        for version in ('rest','grpc'):
            group=[r for r in results if r['concurrency']==concurrency and r['transport']==version]
            valid=len(group)==6 and all(r['valid'] for r in group)
            if not valid:
                lines.append(f'| {concurrency} | {version.upper()} | GEÇERSİZ | — | — | — | Hataları incele |')
                continue
            m=lambda key:statistics.median([key(r) for r in group])
            med[version]=m(lambda r:r['total_seconds'])
            lines.append(f"| {concurrency} | {version.upper()} | {med[version]*1000:.3f} | {m(lambda r:r['successful_rps']):.2f} | {m(lambda r:r['metrics']['median_ms']):.3f} | {m(lambda r:r['metrics']['p95_ms']):.3f} | {sum(r['metrics']['success'] for r in group)} / {sum(r['metrics']['errors'] for r in group)} |")
        if len(med)==2:
            winner=min(med,key=med.get);loser=max(med,key=med.get)
            difference=(med[loser]-med[winner])/med[loser]*100
            comparisons.append(f'- Eşzamanlılık **{concurrency}**: {winner.upper()} bu ölçümde daha kısa sürdü; {loser.upper()}’ye göre ortanca toplam süre **%{difference:.2f} daha düşük** ({med[loser]/med[winner]:.3f}× süre oranı).')
        else:comparisons.append(f'- Eşzamanlılık {concurrency}: eksik/hatalı koşu nedeniyle hız üstünlüğü sonucu çıkarılmadı.')
    lines+=['','## Bu makinedeki karşılaştırma','']+comparisons
    lines+=['','## İşlem türüne göre sonuçlar','',
            'Her hücre, altı koşunun işlem bazındaki değerlerinin ortancasıdır; gecikmeler başarılı istekler üzerinden hesaplanır.','',
            '| Eşzamanlılık | İşlem | REST ortalama / ortanca / p95 ms | gRPC ortalama / ortanca / p95 ms |','| --- | --- | --- | --- |']
    names=[g[0]['resource']+'.'+g[0]['action'] for g in metadata['workload']]
    for concurrency in (1,10):
        for name in names:
            cells=[]
            for version in ('rest','grpc'):
                group=[r for r in results if r['concurrency']==concurrency and r['transport']==version]
                if len(group)!=6 or not all(r['valid'] for r in group):cells.append('GEÇERSİZ');continue
                cells.append(' / '.join(f"{statistics.median([r['operations'][name][k] for r in group]):.3f}" for k in ('mean_ms','median_ms','p95_ms')))
            lines.append(f'| {concurrency} | {name} | {cells[0]} | {cells[1]} |')
    lines+=['','## Her tekrar','', '| Yük | Tekrar | Sıra | Sürüm | Süre (ms) | Hata | Son veri kontrolü |','| --- | --- | --- | --- | ---: | ---: | --- |']
    for r in results:
        lines.append(f"| {r['concurrency']} | {r['repeat']} | {r['position']} | {r['transport']} | {r.get('total_seconds',0)*1000:.3f} | {r.get('metrics',{}).get('errors','—')} | {'Geçti' if r['valid'] else 'BAŞARISIZ: '+r.get('failure','')} |")
    lines+=['','## Yöntem ve sınırlar','',
      '- İstek başına ayrı curl/grpcurl işlemi açılmadı. Aynı Go ölçüm programı HTTP istemcisini ve gRPC istemci bağlantısını yeniden kullandı. HTTP istemcisinde yük kadar bağlantıya izin verildi; gRPC tek bağlantıda çağrıları çokladı.',
      f'- Önce User list/get/create/update/delete; ardından HR için aynı sıra. Her grup {requests//10} istektir; bir grup bitmeden sonraki başlamaz. Listeleme bütün kayıtları döndürür; {requests} isteklik karışımın {requests//5} tanesi büyük liste cevabıdır.', '- Büyük yanıtlar bellekte birikmesin diye gruplar 50 istekli alt partilerle yürütülür. Her alt parti sonrası süre dışında içerik doğrulaması yapılır ve yanıt nesneleri serbest bırakılır. Toplam süre ölçülen alt parti sürelerinin toplamıdır.',
      '- Her koşu yeni servis süreçleri ve seed kopyalarıyla başladı. On User ve on HR okumasıyla ısındı. Sıralı yükte bir, paralel yükte en fazla on istek çalıştı. REST/gRPC sırası tekrarlar arasında değişti.',
      '- İstek gecikmesi JSON/protobuf hazırlama, ağ, Gateway, servis, SQLite ve cevabın tamamen okunup çözülmesini kapsar. Havuzda sıra bekleme bireysel gecikmede yoktur; grup duvar süresine dahildir.',
      '- Derleme, migration, seed kopyalama, port hazır olma kontrolü, ısınma, cevap içeriği karşılaştırması ve rapor yazımı ölçüm dışında tutuldu. Grup duvar süresinde işçi zamanlaması ve zamanlayıcı temizliği bulunur.',
      '- İstek başına beş saniyelik sınır vardır; benchmark düzeyinde tekrar deneme yoktur. Başarısız istekler CSV’de saklanır. Başarısız koşu hız karşılaştırmasına alınmaz.',
      '- İçerik ve kayıt sayıları doğrulandı. Paralel eklemelerde otomatik ID’nin hangi girdiye verileceği değişebilir; başlangıç dosyaları byte düzeyinde aynıdır, son yeni kayıtlar alan içerikleriyle karşılaştırılır. Mevcut kayıtların ID’leri de doğrulanır.',
      '- Bu sonuç yalnızca bu makinedeki mevcut uygulama/iş yükü içindir. SQLite ve Gateway süreleri dahildir; saf protokol benchmark’ı veya istatistiksel üstünlük kanıtı değildir. CPU sabitlenmedi, işletim sistemi disk önbelleği temizlenmedi, arka plan yükü engellenmedi.',
      '- Servis kodundaki tek SQLite bağlantısı ve HTTP/gRPC Gateway uygulama farkları aynen korundu. Yerel şifrelemesiz bağlantılar kullanıldı.',
      '', '## Seed doğrulaması','',f"- User SHA-256: `{manifest['files']['users.db']}`",f"- HR SHA-256: `{manifest['files']['hr.db']}`",f"- REST ve gRPC başlangıç kopyaları bu hash değerleriyle doğrulandı. Her geçerli koşu sonunda {manifest['users']} kullanıcı ve {manifest['leaves']} izin kaldı.", f"- Seed boyutları: User {manifest['bytes']['users.db']:,} byte; HR {manifest['bytes']['hr.db']:,} byte.", '- Tablolar uygulamanın Goose SQL migration dosyalarıyla oluşturuldu. Python yalnızca INSERT ile seed verilerini ekledi. User için 000001_create_users ve 000002_add_address; HR için 000001_create_leaves uygulandı. Seed klasöründeki migration-status.json, Goose status çıktısını içerir.',
      '', '## Ortam ve dosyalar','',f"- CPU: {metadata['environment']['cpu']}",f"- Go: {metadata['environment']['go']}",f"- Sistem: {metadata['environment']['platform']}",f'- Çıktı klasörü: `{folder}`',f'- `samples.csv`: {total:,} isteğin ham ölçümleri; `summary.json`: bütün koşular ve yöntem/ortam bilgileri.','- Her koşu klasöründe kendi CSV/JSON sonucu, servis logları, kopya hash’leri ve son veritabanları bulunur.']
    (folder/'RAPOR.md').write_text('\n'.join(lines)+'\n')

def main():
    parser=argparse.ArgumentParser(description='Prepare seed databases or run the benchmark.')
    parser.add_argument('--prepare-only',action='store_true',help='Build binaries and prepare migrated seed databases without running measurements.')
    args=parser.parse_args()
    started=dt.datetime.now().astimezone().isoformat()
    stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
    folder=ROOT/'results'/stamp
    env=os.environ.copy()
    # Keep user application paths and shell settings out of isolated measurements.
    for key in ('USER_DB','HR_DB','USER_PORT','HR_PORT','GATEWAY_PORT','USER_SERVICE_ADDR','HR_SERVICE_ADDR','TEST_GATEWAY_ADDR'):
        env.pop(key,None)
    print('Building benchmark and service binaries...',flush=True)
    build()
    seed,manifest,data=prepare_seed(env)
    if args.prepare_only:
        print('Seed ready: '+str(seed),flush=True)
        return
    folder.mkdir(parents=True)
    groups=json.loads(execute([ROOT/'bin/bench','workload'],capture_output=True,text=True).stdout)
    metadata={'started_at':started,'environment':describe_environment(),'source_sha256':source_hashes(),'seed':manifest,
              'requests_per_run':sum(len(g) for g in groups),'response_batch_size':50,'warmup_per_run':20,'repeats':6,'concurrency':[1,10],'workload':groups}
    save(folder/'metadata.json',metadata)
    for version in ('rest','grpc'):copy_seed(seed,folder/'datasets'/version,manifest)
    ports=free_ports()
    metadata['ports']={'gateway':ports[0],'user':ports[1],'hr':ports[2]}
    results=[]
    for concurrency in (1,10):
        for repeat in range(1,7):
            order=('rest','grpc') if repeat%2 else ('grpc','rest')
            for position,version in enumerate(order,1):
                run_dir=folder/f'c{concurrency}-r{repeat}-{version}'
                hashes=copy_seed(seed,run_dir/'db',manifest)
                save(run_dir/'initial_sha256.json',hashes)
                runenv=env|{'GATEWAY_PORT':str(ports[0]),'USER_PORT':str(ports[1]),'HR_PORT':str(ports[2]),
                    'USER_SERVICE_ADDR':f'127.0.0.1:{ports[1]}','HR_SERVICE_ADDR':f'127.0.0.1:{ports[2]}',
                    'USER_DB':str(run_dir/'db/users.db'),'HR_DB':str(run_dir/'db/hr.db')}
                record={'concurrency':concurrency,'repeat':repeat,'position':position,'transport':version,'valid':False}
                try:
                    def measured():
                        with open(run_dir/'benchmark.log','w') as log:
                            return execute([ROOT/'bin/bench','run','-transport',version,'-addr',f'127.0.0.1:{ports[0]}','-concurrency',concurrency,'-out',run_dir],env=runenv,stdout=log,stderr=subprocess.STDOUT,timeout=max(180,metadata['requests_per_run']*.2))
                    run_services(version,runenv,run_dir,ports,measured)
                    record.update(json.loads((run_dir/'summary.json').read_text()))
                    record['final_database']=check_final(run_dir/'db',data,groups)
                    record['valid']=record['metrics']['errors']==0
                except (RuntimeError,subprocess.SubprocessError,ValueError,sqlite3.Error) as error:
                    if (run_dir/'summary.json').exists():record.update(json.loads((run_dir/'summary.json').read_text()))
                    record['failure']=str(error)
                record['directory']=str(run_dir)
                results.append(record)
                save(folder/'summary.json',{'metadata':metadata,'runs':results})
                print(f"c={concurrency} repeat={repeat} {version}: {'OK' if record['valid'] else 'FAILED'} {record.get('total_seconds',0)*1000:.2f} ms",flush=True)
    with open(folder/'samples.csv','w',newline='') as output:
        writer=csv.writer(output);writer.writerow(['concurrency','repeat','position','transport','index','operation','latency_ms','success','error'])
        for r in results:
            path=Path(r['directory'])/'samples.csv'
            if not path.exists():continue
            with open(path,newline='') as source:
                rows=csv.reader(source);next(rows)
                for row in rows:writer.writerow([r['concurrency'],r['repeat'],r['position'],r['transport']]+row)
    metadata['finished_at']=dt.datetime.now().astimezone().isoformat()
    metadata['environment']['load_average_end']=os.getloadavg()
    save(folder/'summary.json',{'metadata':metadata,'runs':results})
    save(folder/'metadata.json',metadata)
    report(results,folder,manifest,metadata)
    desktop=Path(subprocess.check_output(['xdg-user-dir','DESKTOP'],text=True).strip()) if shutil.which('xdg-user-dir') else Path.home()/'Desktop'
    desktop.mkdir(exist_ok=True)
    shutil.copy2(folder/'RAPOR.md',desktop/f'REST_gRPC_Benchmark_Sonuclari_{stamp}.md')
    save(ROOT/'results/latest.json',{'directory':str(folder),'desktop_report':str(desktop/f'REST_gRPC_Benchmark_Sonuclari_{stamp}.md')})
    print('Report: '+str(folder/'RAPOR.md'),flush=True)
    if not all(r['valid'] for r in results):raise SystemExit('Some runs failed. See summary/logs; no winner is claimed for affected comparisons.')

if __name__=='__main__':
    main()
