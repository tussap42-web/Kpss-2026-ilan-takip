# Paket A — Ölçülmüş önce / sonra

9 Kasım 2026. Karşılaştırma tabanı: `e4ac5ee952ea1a67ad2e1356f180044b38e4c38a`.
Bu dosya ayrı dalda hazırlanan değişikliği anlatır; ana dala uygulanmış veya canlıya alınmış olduğu anlamına gelmez.

## Aynı örneklerde ölçülen davranış

Karşılaştırma eski ve yeni modülleri ayrı Python süreçlerinde, aynı sentetik P93/70 profili ve 8 Kasım 2026 değerlendirme tarihiyle çalıştırdı. Gönderimler taklittir; iki taklit alıcı kullanıldı. Gerçek kişiler veya Telegram’a canlı gönderim yoktur.

| Örnek | Önce | Sonra |
|---|---|---|
| Tek büro kadrosu P3 isterken profil P93 | Kadro reddedilse de üst ilan review; 2 taklit mesaj | Üst ilan excluded; 0 mesaj |
| Tek büro kadrosunda P93 en az 75 | Üst ilan review; 2 taklit mesaj | Üst ilan excluded; 0 mesaj |
| SBB belgesindeki açık başlangıç/bitiş cümlesi | Son tarih yok; review | 05.11.2026 18:00; süresi geçmiş olarak elenir |
| Konya zabıta alımının gerçek PDF’si | Son tarih yok; review | 04.06.2026 tarihi okunur; süresi geçmiş olarak elenir |
| Hazirandaki Konya haberi, yıl içermeyen kapanış cümlesi | 2 taklit mesaj | Yıl tahmini yapılmadan karantina; 0 mesaj |
| Sözleşmeli personel hakkındaki genelge | İlan adayı kabul edilir | Yeni alım olarak kabul edilmez |
| 2 keşif, ayrıntı bütçesi 1; sonraki taramada liste boş | 1 keşif kalır, diğer kayıt kaybolur | 2 keşif korunur; ikincisi sonraki çalışmada işlenir |
| Public preview çıktısına verilen ayırt edici puan | Puan görünür | Puan ve kişisel uygunluk gerekçesi görünmez |
| Kısmi kaynak hatası | Uygulama özeti kısmi hata; işlem sıfır kodla bitebilir | Bildirimler işlenir ve geçmiş kaydedilir; çıkış kodu 1, sağlık özeti ve hata sayacı |

Konya PDF’sinin kadroları iki sürümde de tam ayrıştırılamadı: **0 → 0 yapılandırılmış kadro**. İyileşme burada tam başvuru döneminin okunmasıdır; PDF kadro çözümlemesinin tamamlandığı iddia edilmiyor. Açık ve basit tabloda “En az 75 Puan” gibi puan hücrelerinin okunması ayrıca regresyon testiyle doğrulandı.

## Testler

- Önceki 54 testin tamamı değişmeden geçti.
- 30 yeni regresyon testi eklendi; toplam 84 test geçti.
- `python -m unittest discover -s tests -v`.
- Yeni testler `tests/test_reliability.py` içinde: yanlış tür/puan, karışık ve kısmi kadrolar, tarihler ve saat, keşif kuyruğu/kesinti, kaynak sağlık geçmişi, özel ayar dosyası, preview gizliliği ve eski alındı uyumu.
- Gerçek resmî HTML/PDF baytlarıyla yerel karşılaştırma yapıldı; bu belgelerin tam kopyaları ve kullanıcı profili depoya eklenmedi. SBB tarihi kısa belge cümlesiyle sınandı; tam PDF yerel işleme başarısı iddia edilmiyor.

## Sınırlar ve korunmuş davranışlar

Eksik belgeyle bütün ilan kesin elenmez. Tam okunmuş bir belgede açık başvuru aralığı, kadro tablosunun belirsizliğinden ayrı tutulur. Birden çok kadroya ait çelişkili tarihler birleştirilmez. Yıl içermeyen haber tarihine yayımlanma yılını ekleyerek uygunluk kararı verilmez.

İlan adayı filtreleme hâlâ kurallara dayanır; bütün kurumları ve bütün başlık biçimlerini kapsamaz. Sonuç/iptal/düzeltmeleri eski bir ilana bağlayıp güncelleme bildirimi üretmek sonraki aşamadır. `/haber/` dışındaki tarihi bilinmeyen kayıtlar, açık olduğu garanti edilmeden incelemeye gönderilebilir.

Doğrudan PDF bağlantısı okunamıyorsa veya eksik okunmuşsa yalnız başlığına dayanarak bildirim gönderilmez; belge doğrulama kuyruğunda tutulur. Canlı kontrolde Konya itfaiye PDF'si erişim/politika nedeniyle okunamadı. Bu kaynakta kapanış tarihi doğrulanmış gibi gösterilmedi.

Tek ortak aday, 20 toplam deneme bütçesi, cron, production secrets ve eski teslimat parmak izleri korunur. Çok kullanıcılı profil, /stop-/sil, token değişiminden bağımsız kimlik, Telegram 429 kuyruğu, özel veritabanı, git push kaybına dayanıklılık, kapsam genişletme ve WhatsApp bu paketle tamamlanmadı.

Özel profil dosyası desteği ve CI’da sentetik profil kullanımı eklendi; gerçek profili public config/git geçmişinden çıkarma ve mevcut türev kullanıcı verilerini özel saklamaya taşıma henüz yapılmadı. Bu nedenle genel kullanıma açılış onayı yerine geçmez.

Production iş akışının saatleri ve state kaydetme adımı değiştirilmez. Kısmi kaynak hatası artık başarısız çıkış verdiğinde mevcut `always()` state kaydetme adımı yine çalışır. Dış kaynaklara canlı PR preview tanısı, kaynak erişimine bağlı olarak uyarı verebilir; bu adım `continue-on-error` ile birim testlerinden ayrılmıştır. Bu ayar tarama kapsamının sağlıklı olduğu iddiası değildir.

## Resmî örnek kaynakları

- [Konya zabıta haberi](https://www.konya.bel.tr/haber/konya-buyuksehirde-zabita-memuru-alimi-icin-basvurular-devam-ediyor).
- [Konya zabıta alımı PDF](https://sepet.konya.bel.tr/kbb/kbb/dosya/duyuru/57/RCijwBf3za2D1K4zITmiUtfjJgWXumiTrhOmMOeb.pdf).
- [SBB bilişim personeli alımı PDF](https://www.sbb.gov.tr/wp-content/uploads/2026/10/SBB-2026-Sozlesmeli-Bilisim-Personeli-Sinav-Ilani.pdf).

Bu sonuçlar örnek bazında ölçümdür; Türkiye genelindeki kaçırma/yanlış eşleşme oranı veya yük testi değildir.
