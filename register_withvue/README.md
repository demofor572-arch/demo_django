# ITLine Django — Yangiliklar (Coin + Manager)

## Qo'shilgan narsalar

### 1. `Manager` modeli — eng yuqori daraja

- Admin va Teacher dan **baland** turadi
- Tizimga to'liq kirish huquqi
- Alohida login endpoint orqali kiradi
- `is_active` maydoni bilan deaktivatsiya qilish mumkin

### 2. `Coin` tizimi

- Har bir studentda `coin_balance` maydoni (tezkor ko'rish)
- `CoinTransaction` modeli — har bir operatsiyaning to'liq tarixi
- Teacher yoki Manager coin bera/ola oladi
- Balans **hech qachon manfiy bo'lmaydi** (tekshiruv bor)
- Manager balansni to'g'ridan-to'g'ri o'zgartira oladi

---

## O'rnatish

### 1. Fayllarni nusxalash

```
models.py   → register_withvue/models.py     (almashtiriladi)
views.py    → register_withvue/views.py      (almashtiriladi)
urls.py     → register_withvue/urls.py       (almashtiriladi)
admin.py    → register_withvue/admin.py      (almashtiriladi)
0003_manager_coin.py → register_withvue/migrations/0003_manager_coin.py  (yangi)
```

### 2. Migration ishlatish

```bash
python manage.py migrate
```

### 3. Birinchi menejer yaratish

```bash
# API orqali:
POST /api/managers/register/
{
  "name": "Sardor",
  "surname": "Toshmatov",
  "phone": "+998901234567",
  "password": "kuchliParol123"
}
```

---

## API Endpointlar

### Manager

| Method | URL                          | Tavsif                 |
| ------ | ---------------------------- | ---------------------- |
| GET    | `/api/managers/`             | Barcha menejerlar      |
| POST   | `/api/managers/register/`    | Yangi menejer yaratish |
| POST   | `/api/managers/login/`       | Menejer login          |
| PATCH  | `/api/managers/update/<id>/` | Ma'lumot yangilash     |
| DELETE | `/api/managers/delete/<id>/` | Deaktivatsiya          |

**Login so'rovi:**

```json
POST /api/managers/login/
{
  "phone": "+998901234567",
  "password": "kuchliParol123"
}
```

**Javob:**

```json
{
  "id": 1,
  "name": "Sardor",
  "surname": "Toshmatov",
  "phone": "+998901234567",
  "role": "manager",
  "is_active": true
}
```

---

### Coin

| Method | URL                                     | Tavsif                                        |
| ------ | --------------------------------------- | --------------------------------------------- |
| GET    | `/api/coins/`                           | Hammaning balansi (`?teacher_id=X`)           |
| GET    | `/api/coins/balance/<student_id>/`      | Bitta student balansi                         |
| GET    | `/api/coins/transactions/<student_id>/` | Student coin tarixi                           |
| POST   | `/api/coins/add/`                       | Coin berish yoki olish                        |
| PATCH  | `/api/coins/set/<student_id>/`          | Balansni to'g'ridan belgilash (faqat manager) |
| DELETE | `/api/coins/delete/<txn_id>/`           | Tranzaksiyani bekor qilish                    |

**Coin berish (Teacher tomonidan):**

```json
POST /api/coins/add/
{
  "student_id": 5,
  "amount": 10,
  "reason": "reward",
  "description": "Darsda faol qatnashdi",
  "teacher_id": 2
}
```

**Coin berish (Manager tomonidan):**

```json
POST /api/coins/add/
{
  "student_id": 5,
  "amount": 50,
  "reason": "manual",
  "description": "Olimpiada g'olibi",
  "manager_id": 1
}
```

**Coin olish (jarima):**

```json
POST /api/coins/add/
{
  "student_id": 5,
  "amount": -5,
  "reason": "penalty",
  "description": "Uy ishini bajarmadi",
  "teacher_id": 2
}
```

**Balansni to'g'ridan belgilash (faqat manager):**

```json
PATCH /api/coins/set/5/
{
  "coin_balance": 100,
  "manager_id": 1
}
```

**Coin tarixi javobi:**

```json
{
  "student_id": 5,
  "student_name": "Ali Valiyev",
  "coin_balance": 85,
  "transactions": [
    {
      "id": 12,
      "amount": 10,
      "reason": "reward",
      "reason_display": "Mukofot",
      "description": "Darsda faol",
      "given_by": "Jasur Karimov",
      "given_by_role": "teacher",
      "created_at": "2026-06-20 14:30"
    }
  ]
}
```

---

## `reason` turlari (Coin)

| Kod          | Ma'no         |
| ------------ | ------------- |
| `reward`     | Mukofot       |
| `attendance` | Davomat uchun |
| `homework`   | Uy ishi uchun |
| `behavior`   | Xulq-atvor    |

| `penalty` | Jarima |
| `manual` | Qo'lda kiritilgan |
| `other` | Boshqa |

---

## Huquqlar darajasi (yuqoridan pastga)

```
Manager  ← hamma narsaga ruxsat, coin balansni to'g'ridan o'zgartirish
Admin    ← mavjud funksional (is_admin=True student)
Teacher  ← o'z studentlariga coin berish, darslar, davomat
Student  ← o'z ma'lumotlarini ko'rish
```

---

## `get_students` javobida yangilik

Endi `coin_balance` maydoni ham qaytadi:

```json
{
  "id": 5,
  "name": "Ali",
  "surname": "Valiyev",
  ...
  "coin_balance": 85
}
```

`login_student` va `register_student` ham `coin_balance` qaytaradi.

sqsbnwqjksbxkjqwbdxkbqwuidbkxuiqwkdbciqwejkdb

{
"id": 5,
"name": "Ali",
"surname": "Valiyev",
...
"coin_balance": 85
}

```

`login_student` va `register_student` ham `coin_balance` qaytaradi.

sqsbnwqjksbxkjqwbdxkbqwuidbkxuiqwkdbciqwejkdb
```
