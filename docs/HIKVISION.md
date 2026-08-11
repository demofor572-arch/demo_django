# Hikvision DS-K1T342EX — sozlash yo'riqnomasi

Yuz tanish terminalini saytga ulash. Oxirida: o'quvchi botga rasm
yuboradi → terminalga avtomatik yoziladi → darsga kirganda davomat
o'zi belgilanadi.

Kerak bo'ladi:

- Terminal (quti ichida: qurilma, kronshteyn, vintlar, quvvat simi)
- Wi-Fi yoki LAN kabeli
- Telefon yoki noutbuk (bir tarmoqda)

---

## Umumiy sxema

```
O'quvchi (Telegram bot)
      │  yuz rasmi
      ▼
   SERVER  ──── takrorlanmas raqam (employee No) beradi
      │  ▲
      │  │ ① hodisa: "10042 raqamli odam tanildi"
      │  │
      ▼  │
   TERMINAL  ── ② navbatdan yuzlarni oladi
```

Ikki bog'lanish bor va ular alohida ishlaydi:

1. **Terminal → server.** Yuz tanilganda terminal serverga xabar
   yuboradi, server davomatni belgilaydi. Buning uchun terminal
   internetga chiqsa bo'ldi — statik IP kerak emas.
2. **Server → terminal.** Yangi yuzlarni terminalga yozish. Terminalga
   tashqaridan kirib bo'lmasa (odatiy holat), buni terminal yonidagi
   kompyuterdagi kichik skript qiladi.

---

## 1-qadam. Terminalni yoqish va parol qo'yish

1. Quvvat simini ulang. Terminal 30–40 soniyada yonadi.
2. Ekranda **Activation** (faollashtirish) so'raladi — parol
   o'ylab toping.
   - Kamida 8 belgi, katta-kichik harf va raqam aralash.
   - **Bu parolni yozib qo'ying.** Uni tiklab bo'lmaydi — unutsangiz
     terminalni zavod holatiga qaytarish kerak bo'ladi.
3. Parolni tasdiqlagach terminal asosiy ekranga o'tadi.

> Ekranga kirish uchun: asosiy ekranni **bosib turing** → parolni
> kiriting → sozlamalar menyusi ochiladi.

---

## 2-qadam. Tarmoqqa ulash

Sozlamalarda **Network** bo'limiga kiring.

**Wi-Fi orqali:**

1. `Wi-Fi` → ro'yxatdan tarmoqni tanlang → parolni kiriting.
2. Ulangach `Wired Network` ni o'chirib qo'ying (ikkalasi yoqiq
   bo'lsa terminal qaysi biridan chiqishni chalkashtiradi).

**LAN kabeli orqali:**

1. Kabelni ulang.
2. `Wired Network` → `DHCP` ni yoqing — IP avtomatik olinadi.

**IP manzilni yozib oling.** U `Network` bo'limida ko'rinadi, masalan
`192.168.1.64`. Keyingi qadamlarda kerak bo'ladi.

Internet borligini tekshirish: bir tarmoqdagi noutbuk brauzerida
`http://192.168.1.64` ni oching — terminalning veb-oynasi chiqishi
kerak (login: `admin`, parol — 1-qadamdagi parol).

---

## 3-qadam. Vaqtni to'g'rilash ⚠️

**Bu qadamni o'tkazib yubormang.** Terminal vaqti noto'g'ri bo'lsa
o'z vaqtida kelgan o'quvchi «kech keldi» bo'lib belgilanadi va
coinlari kamayadi.

1. `System` → `Time`
2. **Time Zone:** `GMT+05:00` (Toshkent)
3. **NTP** ni yoqing, server: `pool.ntp.org`, port `123`
4. NTP ishlamasa vaqtni qo'lda kiriting va oyda bir tekshirib turing.

---

## 4-qadam. Hodisa manzilini kiritish (asosiy qadam)

Terminal yuzni tanigach serverga xabar yuborishi kerak.

**Manzilni oling:**

Panel → **Yuz tanish** → «Terminal qo'shish» → nomini yozing
(masalan «Kirish eshigi») → saqlang. Kartochkada manzil chiqadi:

```
https://itline-django-9s85.onrender.com/api/faceid/event/KALIT/
```

Bu manzil har bir terminal uchun alohida va maxfiy — uni faqat
terminal sozlamasiga yozing.

**Terminalga kiritish** (ikki yo'l bor, biri yetadi):

### A. Terminal ekranidan

`Network` → `HTTP Listening` (proshivkada `Event` → `Notification`
bo'lishi ham mumkin):

| Maydon        | Qiymat                                     |
| ------------- | ------------------------------------------ |
| IP / Domain   | `itline-django-9s85.onrender.com`          |
| Port          | `443`                                      |
| URL           | `/api/faceid/event/KALIT/`                 |
| Protocol      | `HTTPS`                                    |
| Format        | `JSON`                                     |

### B. Veb-oyna orqali (osonroq)

1. Brauzerda `http://192.168.1.64` → `admin` + parol.
2. `Configuration` → `Network` → `Advanced Settings` → `HTTP Listening`.
3. Yuqoridagi jadvaldagidek to'ldiring → `Save`.

**Tekshirish:** panelda terminal kartochkasidagi «Sinov hodisasi»
tugmasini bosing. «Zanjir ishlayapti» degan javob kelsa manzil
to'g'ri. Keyin haqiqiy yuz bilan sinab ko'ring — hodisa «Oxirgi
hodisalar» ro'yxatida chiqishi kerak.

---

## 5-qadam. Yuzlarni yig'ish (bot orqali)

Qo'lda hech narsa qilish shart emas:

1. O'quvchi botga kiradi, `/start` → telefon raqamini yuboradi.
2. **🪪 Face ID** tugmasini bosadi.
3. Bot rasm talablarini va ogohlantirishni ko'rsatadi.
4. O'quvchi yuzining 4:3 formatdagi rasmini yuboradi.
5. Server unga **takrorlanmas raqam** beradi (`employee No`) va rasmni
   terminalga yozish navbatiga qo'yadi.

Rasm talablari (bot o'zi tekshiradi):

- O'lchami 4:3 yoki 3:4 (kvadrat va 16:9 rad etiladi)
- Kamida 240 nuqta
- Server rasmni 1024 nuqta va 200 KB ichiga o'zi keltiradi

Supermenejer panelda **«Bot orqali kelgan yuzlar»** bo'limida
rasmlarni ko'radi va yaroqsizini rad eta oladi — o'quvchiga sabab
avtomatik yuboriladi.

---

## 6-qadam. Yuzlarni terminalga yozish

Ikki yo'ldan biri. Terminalingiz oddiy lokal tarmoqda bo'lsa — **B**.

### A. Panel orqali (terminalga tashqaridan kirish ochiq bo'lsa)

Bu faqat statik IP yoki port forwarding sozlangan bo'lsa ishlaydi.

1. Panelda terminalni tahrirlang.
2. «Saytdan terminalga yuborish» bo'limini to'ldiring:
   - Manzil: `http://SIZNING_TASHQI_IP:80`
   - Login: `admin`
   - Parol: 1-qadamdagi parol
3. Saqlang → kartochkada **«Yozish»** tugmasi paydo bo'ladi.

### B. Lokal agent orqali (tavsiya etiladi)

Terminal bilan bir tarmoqdagi kompyuterda (kassa kompyuteri bo'lsa
ham bo'ladi) ishlaydigan kichik skript.

1. Kompyuterga Python o'rnating (python.org).
2. `tools/hik_agent.py` faylini o'sha kompyuterga ko'chiring.
3. Buyruq qatorida:

```bash
pip install requests
```

4. Panelda terminal kartochkasidan **sinxronlash manzilini** nusxa
   oling va ishga tushiring:

```bash
python hik_agent.py --sync-url "https://SERVER/api/faceid/sync/KALIT/" --host http://192.168.1.64 --user admin --password TERMINAL_PAROLI
```

Skript har 60 soniyada navbatni tekshiradi va yangi yuzlarni
terminalga yozadi. Oynani yopmang — kompyuter yoqilganda o'zi
ishga tushishi uchun uni avtoyuklanishga qo'ying.

Bir marta ishlatib ko'rish uchun oxiriga `--once` qo'shing.

---

## 7-qadam. Tekshirish

1. Panel → **Yuz tanish** → «Bot orqali kelgan yuzlar» — o'quvchi
   holati **«Terminalga yozilgan»** bo'lsin.
2. O'quvchiga botga «Face ID tayyor» xabari kelgan bo'lishi kerak.
3. O'quvchi terminalga yuzini ko'rsatsin.
4. «Oxirgi hodisalar» ro'yxatida **«Davomat belgilandi»** chiqsin.

---

## Nima ishlamayotgan bo'lsa

**Terminal hodisa yubormayapti**

- Terminal internetga chiqayaptimi: `Network` bo'limida IP bormi.
- Manzil to'g'ri yozilganmi — panelda «Sinov hodisasi» ni bosib
  ko'ring. U ishlasa muammo terminalda, ishlamasa serverda.
- Port `443` va protokol `HTTPS` ekanini tekshiring.
- Eski proshivkalar HTTPS ni qo'llamaydi. O'shanda proshivkani
  yangilang yoki hodisalarni HTTP orqali qabul qiladigan oraliq
  kompyuterdan foydalaning.

**Hodisa kelyapti, lekin davomat belgilanmayapti**

Panelda hodisa izohiga qarang:

| Izoh                       | Sababi                                            |
| -------------------------- | ------------------------------------------------- |
| O'quvchi topilmadi         | Raqam hech kimga bog'lanmagan — «Bog'lanmagan» bo'limidan biriktiring |
| Bugun dars yo'q            | Guruh jadvalida bugun dars yo'q                   |
| Guruhga biriktirilmagan    | O'quvchi hech qaysi guruhda emas                  |
| Allaqachon belgilangan     | Ustoz qo'lda belgilab qo'ygan — bu xato emas      |

**Terminal rasmni qabul qilmayapti**

Xato paneldagi rasm kartochkasida ko'rinadi:

- *Rasmda yuz topilmadi* — yuz kichik yoki qorong'i, yangi rasm kerak
- *Yuz sifati past* — yorug'roq joyda qayta suratga olsin
- *Login yoki parol noto'g'ri* — terminal parolini panelda yangilang

**Terminal «kech keldi» deb belgilayapti**

3-qadamga qayting — vaqt zonasi `GMT+05:00` bo'lishi kerak.

---

## Foydali ma'lumot

- Bir necha terminal qo'shsa bo'ladi (kirish va chiqish eshigi).
  Har biri o'z manzilini oladi va yuzlar hammasiga yoziladi.
- O'quvchi rasmni almashtirsa, terminal yangisini o'zi oladi —
  qo'lda qayta yozish kerak emas.
- Raqamlar (`employee No`) qayta ishlatilmaydi: o'quvchi o'chirilsa
  ham uning raqami boshqa hech kimga berilmaydi.
- Rasmlar bazada saqlanadi, fayl tizimida emas — server qayta
  yuklansa ham yo'qolmaydi.
