# REST / gRPC benchmark

**10.000 kullanıcı, 20.000 izin; her sürümde koşu başına 5.000 istek.**
İki yük seviyesi (1 ve 10 eşzamanlı istek), altışar tekrar: toplam 120.000 istek.

## Çalıştırma sınırları

- Bu depo tek başına çalışmaz; `project-rest` ve `project-grpc` kaynakları gerekir.
- **`project-grpc` özel depodur.** Bu depoya erişimi olmayan kullanıcı benchmark'ı hazırlayıp çalıştıramaz. Kaynakların kurulumu için [Servis kaynaklarını hazırlama](#servis-kaynaklarını-hazırlama) bölümüne bakın.
- Go ve Python 3 gerekir. `.sh` başlatıcıları Bash, `fish` olarak işaretlenmiş terminal örnekleri fish kullanır.
- Farklı kaynak klasörleriyle benchmark ve sekiz servis programının derlenmesi doğrulandı. Bu kontrol, tüm işletim sistemlerinde çalışmanın veya ölçüm sonuçlarının doğrulandığı anlamına gelmez.

## Yalnızca hazırla

Go ve Python 3 gerekir. Tablolar Goose migration ile oluşturulur, ardından seed kayıtları eklenir. Bu komut ölçüm yapmaz:

```fish
cd (git rev-parse --show-toplevel)
./hazirla.sh
```

## Benchmark’ı başlat

```fish
cd (git rev-parse --show-toplevel)
./run.sh
```

Betik hazırlığı da yapar; servisleri ayrı terminalde açman gerekmez. Terminal açık kalsın. Durdurmak için **Ctrl+C** kullan.

- Her koşu aynı seed’in doğrulanmış kopyasıyla başlar; mevcut proje veritabanların kullanılmaz.
- User ve HR için listeleme, okuma, ekleme, güncelleme ve silmeye 500’er istek gönderilir.
- Büyük cevaplar 50’lik alt partilerde işlenir. Hazırlık, ısınma ve içerik kontrolleri ölçüm dışında kalır.
- Seed: `seeds/users-10000-leaves-20000/`. `migration-status.json`, uygulanan Goose migration’larını gösterir.
- Sonuçlar: `results/<zaman>/RAPOR.md`, `samples.csv`, `summary.json`. Rapor masaüstüne de kopyalanır.
- Son tamamlanan çalışmanın yolu: `results/latest.json`. Yarıda durdurulan çalışma tam sonuç sayılmaz.
- Eski küçük seed ve sonuçlar korunur. Yeni profil eski profilden farklı boyut ve alt parti düzeni kullandığı için doğrudan eşdeğer değildir.

Veri sayısını `workload.go` içindeki `seedUsers`, işlem başına istek sayısını `callsPerOperation` belirler. Okuma/güncelleme/silme hedeflerinin ayrılması için kullanıcı sayısını işlem başına istek sayısının en az üç katı tut.

Ölçüm Gateway ve SQLite dahil uygulamanın tamamını kapsar; yalnızca protokol hızını göstermez.

## Proje dizini

Depoyu istediğiniz klasöre klonlayıp o dizine girin. `git rev-parse --show-toplevel` kullanılan komutlar klonun içinden çalıştırılır; kullanıcı adı veya sabit bir ana dizin gerekmez.

## Servis kaynaklarını hazırlama

Benchmark iki ayrı servis deposunun kaynaklarına ihtiyaç duyar. Varsayılan yerleşim:

```text
calisma/
  rest-grpc-benchmark/
  project-rest/
  project-grpc/
```

REST: https://github.com/Archmetrus/project-rest

gRPC: https://github.com/Archmetrus/project-grpc — bu depo özeldir; hesap erişimi gerekir. Erişiminiz yoksa benchmark hazırlanamaz. Depo görünürlüğü bu değişiklikle değiştirilmez.

Farklı konumlar için `PROJECT_REST_DIR` ve `PROJECT_GRPC_DIR` ortam değişkenlerini kullanın (tam yol veya benchmark köküne göre göreli yol, `~` desteklenir). Örnek Bash:

```sh
export PROJECT_REST_DIR="/kaynaklar/project-rest"
export PROJECT_GRPC_DIR="/kaynaklar/project-grpc"
./hazirla.sh
```

Go modülünün yerel gRPC bağlantısı hazırlık sırasında bu ayara göre geçici bir modül dosyasında çözülür; servis kaynaklarının go.mod dosyaları değiştirilmez.
