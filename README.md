# KPSS 2026 İlan Takip

Emre: ön lisans / Grafik Tasarım / P93 / yaklaşık 70,60. Konya öncelikli; diğer şehirler dışlanmaz. Mevcut Telegram secrets ve bildirim geçmişi kullanılır.

## İlan keşfi ve gerçek kapsam

13 kaynak tanımı vardır. Memurlar.net'in `https://ilan.memurlar.net/` ilan sayfası yardımcı kaynak olarak eklenmiştir; resmî kurum kaynağı değildir. Bu sitedeki `/ilan/<sayısal kimlik>/<başlık>.html` bağlantılarında “alacak” başlıkları da tanınır; haber ve kategori bağlantıları bu ek kuralla ilan sayılmaz. Başvuru koşulları resmî kurum ilanından kontrol edilmelidir. Erişim ve robots.txt kuralları diğer kaynaklarla aynıdır. ÖSYM duyuruları ve KPSS alt sayfası, Yerel Yönetimler duyuruları ve Konya Büyükşehir İnsan Kaynakları duyuruları başlangıç noktalarına eklenmiştir. Diğer kurumlarda ana sayfada gerçekten bulunan ilan/duyuru menüleri, `rel=next` veya açık “Sonraki” bağlantıları ve ilan edilmiş RSS/Atom akışları izlenir. Her kaynak için **en çok 4 liste sayfası** okunur; API adresi/sayfa numarası tahmin edilmez. Araştırmada doğrulanmış açık bir ilan API sözleşmesi bulunmadığı için API entegrasyonu iddiası yoktur.

- robots.txt yasakları, crawl-delay ve request-rate dikkate alınır; aynı alan adına en az 1 saniye arayla istek yapılır. Erişim hatası/engel aşılmaz. Robots politikası okunamıyorsa kaynak ihtiyatla atlanır; 404/410 robots yok sayılır. Bu tercih bazı kaynaklarda kapsamı azaltabilir.
- HTML/XML liste sayfaları ve aynı site/alt alanındaki metin PDF'leri desteklenir. JavaScript, giriş, CAPTCHA, OCR gerektiren portallar kapsam dışındadır.
- SOURCE kayıtlarında denenen/okunan/hatalı liste sayfaları ve ilan adayı sayısı yazılır. “Sayfa okundu” veya “0 ilan” tüm kurum ilanlarının kapsandığı anlamına gelmez. 81 ilin bütün kurumlarını kapsama iddiası yoktur.
- URL parametreleri normalleştirilir. Tam eşleşen belge kümeleri/içerikler, güvenli parmak iziyle kaynaklar arasında birleştirilir; yalnızca başlık benzerliğiyle farklı ilanlar birleştirilmez. Eski URL alındıları korunur. Farklı biçimde yayımlanmış aynı ilan yine ayrı kalabilir.

## Uygunluk ve belgeler

Açık tek KPSS taban puanı/puan türü ve sayısal veya Türkçe ay adlarıyla belirtilen son başvuru tarihleri değerlendirilir. Şu örnekler desteklenir:

- `Son başvuru: 15.11.2026`
- `Başvuru bitiş tarihi: 15/11/2026`
- `Başvurular 10-15 Kasım 2026 tarihleri arasında alınacaktır.`
- `Son müracaat tarihi 15 Kasım 2026'dır.`

Birden fazla veya belirsiz tarih tek bir son tarih olarak seçilmez. Kadro satırlarındaki tarihler diğer kadrolara aktarılmaz.

HTML tabloları ve düzeni korunarak çıkarılabilen PDF tablolarında **unvan, KPSS puan türü ve taban puan başlıkları açık ve sütun sayıları uyumluysa** satırlar eşleştirilir; mezuniyet ve nitelik kodları o satırdan alınır. Açık tek satırlı metinler de ayrılır. Büro P93 75 / destek P93 60 örneğinde 70,60 puanlı adayın iki satırı ayrı gösterilir. Bir kadronun puanı diğerine taşınmaz.

Karmaşık/birleşik hücreler, kaymış/satırlara bölünmüş tablolar veya eksik PDF'lerde tahmin yapılmaz. Görsel PDF'ler OCR gerektirir. PDF okuma ayrı bir işlemde 20 saniye duvar süresi, Linux'ta 512 MB bellek ve 15 saniye CPU sınırıyla yapılır. En fazla 8 MB liste sayfası, 4 MB ilan belgesi, 40 PDF sayfası, HTML başına 2 PDF okunur; kısıt/eksik ek varsa ilan kesin elenmez. Puan/bölüm bulguları **tam başvuru uygunluğu değildir**; yıl, yaş, sertifika ve tüm özel koşullar resmî belgeden doğrulanmalıdır.

## Bildirim ve durum güvenilirliği

- `max_notifications_per_run=20`, **tüm alıcılar için toplam mesaj denemesi** sınırıdır; alıcı başına veya ilan başına değildir. Hatalı denemeler de bütçeden sayılır. Dönen alıcı sırası, sınır 1 olduğunda bile ilk bozuk alıcının diğerini sürekli engellemesini önler.
- Gönderilmeyen alıcılar/ilanlar sonraki çalışmaya kalır; ana sayfadan kaybolan önbellek ilanları da kuyrukta tutulur. Yeni ayrıntılar, eski günlük önbellek yenilemelerinden önce işlenir. Sınırlar nedeniyle gecikme mümkündür.
- Son başvuruya 3, 1 ve 0 gün kala birer hatırlatma vardır. Önce orijinal bildirimin teslim edilmiş olması ve belgenin aynı gün hatasız yenilenmesi gerekir. Süresi geçmiş veya tarihleri belirsiz ilanlarda genel hatırlatma yoktur.
- Kayıt atomik yazılır ve diske aktarılır. Bozuk geçmiş dosyası sıfırlanmaz; işlem durur. Eski `sent` yapısı desteklenir; geçmiş silinmez.
- Telegram'ın yanıtı alınamadan bağlantı koparsa veya mesaj tesliminden hemen sonra runner ölürse, sonraki denemede mükerrer mesaj olasılığı tamamen ortadan kaldırılamaz; Telegram sendMessage idempotency anahtarı sağlamaz. Bu sistem tam “exactly once” garantisi vermez.

## Zamanlama ve çalıştırma

Cron **`7 6-20 * * *` UTC**, Türkiye **09:07–23:07**. GitHub gecikme/atlama yapabilir. 7 Kasım 2026 22:39 UTC'deki eski schedule çalışması Türkiye'de 8 Kasım 01:39'a denk geldiği için eski saat kontrolünden atlanmıştır; bu bir Telegram arızası değildir. Güncel elle başlatılan workflow `FORCE_RUN=1` kullanır; otomatik çalışma yerel saat aralığını denetler.

`RUN_SUMMARY` ve Actions adım özetinde şu durumlar ayrılır: `success`, `skipped_time_window`, `skipped_notifications_disabled`, `partial_source_error`, `source_error`, `telegram_error`, `configuration_error`, `program_error`. Ön izlemede durumun önüne `preview_` eklenir. Tam kaynak/Telegram hatası başarısız çıkış; kısmi kaynak hatası açık uyarı ve sayımla raporlanır.

Mevcut `TELEGRAM_BOT_TOKEN` / virgülle ayrılmış sayısal `TELEGRAM_CHAT_IDS` secrets değerlerini değiştirmeyin. Alıcılar bota `/start` göndermiş olmalıdır. `find-chats` herkese açık Actions günlüklerine chat ID yazar; yalnız gerektiğinde çalıştırın. Tokenları dosyaya yazmayın. `state/seen.json` varsa koruyun; yoksa ilk gerçek taramada oluşturulur ve commit edilir.

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py --mode preview
```

PR CI, gerçek kaynaklarda **Telegram secrets olmadan** preview çalıştırır ve `preview-log` çıktısını saklar. Preview mesaj göndermez ve state yazmaz. Gerçek gönderim modu `scan`dır. Her taramada en çok 12 ayrıntı incelenir. `config.json` aday profili, bildirim bütçesi ve hatırlatma günlerini yönetir; çalışma saatini değiştirirken workflow cron'u da düzenlenmelidir. `positions` kapsamlı kadro filtresi, `qualification_codes` ise doğrulanmış satır eşleşmesi olmadan kesin uygunluk filtresi değildir. `prevent_duplicates` güvenilir teslimat için daima uygulanır.

Kaynak belgeleri: [ÖSYM duyuruları](https://www.osym.gov.tr/Duyurular/Index), [Yerel Yönetimler](https://yerelyonetimler.csb.gov.tr/duyurular), [Konya İK](https://www.konya.bel.tr/birim/insan-kaynaklari-ve-egitim-dairesi-baskanligi/duyurular), [PDF metin çıkarma sınırları](https://pypdf.readthedocs.io/en/stable/user/extract-text.html), [GitHub zamanlaması](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
