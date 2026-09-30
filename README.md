# REST / gRPC benchmark

**10.000 kullanıcı, 20.000 izin; her sürümde koşu başına 5.000 istek.**
İki yük seviyesi (1 ve 10 eşzamanlı istek), altışar tekrar: toplam 120.000 istek.

## Yalnızca hazırla

Go ve Python 3 gerekir. Tablolar Goose migration ile oluşturulur, ardından seed kayıtları eklenir. Bu komut ölçüm yapmaz:

```fish
cd /home/ykk/PROJE/benchmark
./hazirla.sh
```

## Benchmark’ı başlat

```fish
cd /home/ykk/PROJE/benchmark
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
