# KPSS 2026 İlan Takip

Emre için ön lisans / Grafik Tasarım / P93 / yaklaşık 70,60 puan profiliyle ilan ön elemesi ve Telegram bildirimi. Konya başlıkları önceliklidir; Türkiye geneli kaynaklar dışlanmaz.

## Bu sürüm

- Resmî kaynaklardaki ilan bağlantılarının detay sayfalarını ve aynı sitedeki en fazla iki PDF ekini okur. İlan sonuç/iptal başlıklarını yeni başvuru olarak göndermez.
- Açıkça belirtilmiş tek P93 taban puanı aday puanından yüksekse eler. Tek ve açık farklı puan türünü veya lisans mezuniyeti zorunluluğunu da eler. Örneğin P93 en az 75 elenir; P93 en az 70 incelemeye gönderilir.
- Çok kadrolu/çok puanlı metinlerde kadro satırlarını karıştırmamak için otomatik uygunluk kararı vermez. Okunamayan belgeleri kesin uygun veya uygunsuz saymaz.
- 3001 ve bölüm adının bulunmasını bildirir; bunların aynı kadro satırına ait olduğunu iddia etmez. Yaş, deneyim ve sertifika koşulları insan kontrolü gerektirir.
- Açık “Son başvuru [tarihi]” / “Başvuru bitiş [tarihi]” etiketini izleyen tek sayısal tarihi okur; geçmişse eler. Diğer tarih biçimleri ve çok tarihli metinler incelemeye kalır.
- Bilinen son başvuru için 3, 1 ve 0 gün kala, ilk bildirimi almış kişilere birer hatırlatma gönderir. Tarihi sonradan değişen veya iptal edilen ve ana sayfadan kaldırılan ilanlarda hatırlatma güncelliği garanti edilmez; resmî ilan esas alınmalıdır.
- Alıcı başına gönderim geçmişini tutar. Bir alıcının hatası diğerini engellemez. İlk oluşan geçmiş dosyası da GitHub'a kaydedilir.
- Her taramada en fazla 12 yeni/güncellenecek detay inceler; günde bir önbellek yeniler. Her çalışmada 20 bildirim sınırı vardır. Fazlası sonraki çalışmalara kalır.

## Kurulum / mevcut botu yükseltme

Mevcut `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_IDS` Actions secrets değerlerini koruyun. Alıcılar virgülle ayrılmış sayısal kimliklerdir; alıcılar bota `/start` göndermelidir. Token dosyalara yazılmamalıdır. `state/seen.json` varsa silmeyin; yoksa ilk taramada oluşur. Eski biçim desteklenir.

GitHub Actions gerekli PDF paketini otomatik kurar ve önce testleri çalıştırır. Actions → KPSS Ilan Takip → Run workflow → `preview` mesaj göndermeden kontrol eder; `scan` gerçek taramadır. Elle başlatılan çalışma saat sınırından bağımsızdır. `find-chats` modunun herkese açık Actions günlüklerine chat ID yazdığını unutmayın.

Zamanlama korunmuştur: UTC 06:07–20:07, Türkiye 09:07–23:07. GitHub zamanlaması gecikebilir/atlanabilir; kesin saatlik servis garantisi yoktur. Kısmi kaynak hataları Actions uyarısıdır; tüm kaynaklar başarısızsa işlem hata verir.

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py --mode preview
```

`config.json`: puan, bölüm, bildirimler, detay işleme sınırı, hatırlatma günleri. Otomatik çalışma saatini değiştirmek için workflow cron'u da değiştirin; `interval_minutes` tek başına GitHub zamanlamasını değiştirmez. `positions`, `all_turkey`, `qualification_codes` tanımları kapsamlı kadro filtreleri değildir. `prevent_duplicates` güvenilir teslimat için daima uygulanır. Alıcılar ortam değişkeninden okunur.

## Kapsam

Listelenen sitelerin sunucudan gelen ana sayfa bağlantıları taranır. Bu, 81 ildeki tüm kurumları kapsamaz. JavaScript gerektiren portallar, ayrı alan adındaki ekler ve görsel tarama PDF'leri (OCR) kapsam dışıdır. PDF okuma pypdf ile yapılır; taranmış belgeler için OCR gerekir: https://pypdf.readthedocs.io/en/stable/user/extract-text.html

Adayın tam puanını `kpss_score` alanına girip `score_is_approximate` değerini ancak puan kesinleşince false yapın. Eski kayıt dosyası bulunmadığından ilk yükseltmede bazı ilanların bir kez tekrar gelmesi mümkündür. Puan eşiğini karşılamak başvuruya tümüyle uygun olmak anlamına gelmez.
