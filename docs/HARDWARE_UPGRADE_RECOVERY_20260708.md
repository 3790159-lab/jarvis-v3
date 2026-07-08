# 🔧 Чек-лист восстановления Jarvis после апгрейда железа (1ТБ SSD + 16ГБ RAM)

**Составлен:** 2026-07-08, перед заменой диска/RAM.
**Прод на момент бэкапа:** ветка `phase-4.0-unified-jarvis`, **HEAD `f59f334`** (Brain-арка IR+money-confirm смерджена).
**Полный бэкап:** `jarvis_full_backup_20260708.tar.gz.gpg` (2.2 ГиБ, gpg-AES256) — в облаке (Google Drive), см. чат за ссылкой/подтверждением.
**Парольная фраза gpg:** хранится в твоём менеджере паролей (я дал её в чат). Без неё архив НЕ расшифровать.

---

## 0. Что внутри бэкапа
Top-level: `jarvis/` (весь репо **включая `.git` — полная история**, `state/` целиком, `.env`) + `backup_configs/` (автостарты).
- `jarvis/.git` — история (320 МБ), `jarvis/state/` — леджеры (cost_tracking, users.json, brain_v2_state, regress_baseline, dev_tasks) + LoRA-персоны (1.8 ГБ), `jarvis/.env` — боевые ключи, `jarvis/requirements.txt`.
- `backup_configs/` — `JarvisBotGuardian.xml`, `JarvisBackendGuardian.xml`, `JarvisSniperDetached.xml`, `sshd_config`, `cloudflared_full/` (config.yml + cert.pem + tunnel-creds json + tokens).
- **НЕ в архиве:** `.venv` (переустанавливается из requirements), `__pycache__`, `*.pyc`.

---

## 1. Поднять Jarvis на новом 1ТБ SSD

### Способ: СВЕЖАЯ УСТАНОВКА (рекомендую), не клонирование диска
**Почему свежая установка надёжнее клона:**
- Клон старого диска тащит его мусор: тесный диск копил backup_*-каталоги, осиротевшие worktree, битые кэши; клонирование увековечит проблему, ради которой и делаем апгрейд.
- Новый SSD + чистая ОС/Python = предсказуемое окружение без дрейфа драйверов/битых путей.
- У нас есть **полная git-история + state + .env** — этого достаточно для 100% восстановления; клон не даёт ничего сверх.
- Клон оправдан только при спешке или нетривиальной ручной настройке ОС, которую лень повторять. У нас настройка декларативна (этот чек-лист) → свежая чище.

### Шаги
1. **ОС + базовый софт:** свежая Windows, затем поставить: **Python 3.14.x** (прод был `3.14.4` — ставить ту же minor-линию), Git, OpenSSH (Windows feature), cloudflared, gpg (для расшифровки).
2. **Скачать бэкап из облака** на новый диск (rclone/браузер).
3. **Расшифровать + распаковать:**
   ```bash
   gpg --pinentry-mode loopback --passphrase '<ПАРОЛЬ>' -d jarvis_full_backup_20260708.tar.gz.gpg | tar -xz -C C:/
   # → появятся C:/jarvis  и  C:/backup_configs (или распакуй backup_configs куда удобно)
   ```
4. **Пересоздать `.venv`** (его в архиве нет):
   ```
   cd C:\jarvis
   py -3.14 -m venv .venv
   .venv\Scripts\python.exe -m pip install --upgrade pip
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
   (Если insightface/onnxruntime упрутся — см. requirements_backup_pre_insightface.txt и заметки B-52 про CPU/GPU onnxruntime.)
5. **Проверить HEAD:** `git -C C:\jarvis rev-parse HEAD` → должно быть `f59f334…`. `git status` — код чист (грязь только в `jarvis_stage3_artifacts/` удалениях, это норма).

---

## 2. Восстановить автостарты (порядок важен)
Все три задачи гардианов — из `backup_configs/*.xml`. Импорт:
```powershell
Register-ScheduledTask -Xml (Get-Content backup_configs\JarvisBackendGuardian.xml -Raw) -TaskName JarvisBackendGuardian
Register-ScheduledTask -Xml (Get-Content backup_configs\JarvisBotGuardian.xml -Raw) -TaskName JarvisBotGuardian
Register-ScheduledTask -Xml (Get-Content backup_configs\JarvisSniperDetached.xml -Raw) -TaskName JarvisSniperDetached
```
Эталон задач (проверить после импорта): trigger **AtStartup (BootTrigger)** + LogonTrigger, **RunLevel=Highest**, **LogonType=S4U**, user=Admin → нянька встаёт сама после reboot. (S4U может потребовать заново «привязать» аккаунт при первом Enable на новой машине.)

**Порядок подъёма:**
1. **sshd** — служба, StartType **Automatic** (`sshd_config` из backup_configs → `C:\ProgramData\ssh\sshd_config`, затем `Start-Service sshd`). Нужен для удалённого доступа.
2. **cloudflared** — служба, StartType **Automatic** (на старой машине была Automatic Running). Восстановить `cloudflared_full/` в `C:\Users\<user>\.cloudflared\` (config.yml + cert.pem + `<tunnel-id>.json` креды!), затем `cloudflared service install` + `Start-Service Cloudflared`. Без tunnel-creds json туннель не поднимется.
3. **JarvisBackendGuardian** → поднимает backend :8010.
4. **JarvisBotGuardian** → поднимает бота (AtStartup, Highest; PYTHONUTF8=1 для эмодзи).
5. **JarvisSniperDetached** — по необходимости (RunPod-снайпер, вне критического пути).

---

## 3. Проверить после подъёма (health-gates)
- [ ] **backend:** `curl http://127.0.0.1:8010/health` → `{"status":"healthy"}`.
- [ ] **бот на нужном HEAD:** в Telegram `/health` → строка `🤖 бот: PID … ✅` + `git rev-parse HEAD` = `f59f334…`.
- [ ] **гардиан активен:** `/health` показывает `🛡️ гардиан: активен (Nс назад) ✅` (нянька пишет `state/guardian_heartbeat.txt`); `Get-ScheduledTask Jarvis* | State` = Running.
- [ ] **туннель поднялся:** `Get-Service Cloudflared` = Running; внешний доступ через `ssh.jarvis-d.com` / webhook отвечает.
- [ ] **heartbeat:** `state/bot_heartbeat.txt` растёт (свеж <90с).
- [ ] **генерации работают:** живой тап — `/animate`→`aq:done` (Replicate raw-httpx фикс `f277588` в HEAD; движок `wan-2.2-i2v-fast`) и/или свап; проверить, что ключи из `.env` валидны (некоторые могли протухнуть — WaveSpeed/Replicate баланс).
- [ ] **money-гейт цел:** `/pro_food`/`/gen` требуют confirm; `/animate_batch`/`/dev_task`/`/videoref` идут своим потоком (EXEMPT, фикс в `f59f334`).

---

## 4. Проверить целостность восстановленного из архива
1. **До распаковки** — обратимость шифра и tar:
   ```bash
   gpg --pinentry-mode loopback --passphrase '<ПАРОЛЬ>' -d jarvis_full_backup_20260708.tar.gz.gpg | gzip -t && echo OK
   ```
   (Именно так бэкап и проверялся при создании — decrypt+gzip -t прошёл.)
2. **После распаковки** — git-целостность истории:
   ```
   git -C C:\jarvis fsck --full        # объекты не побиты
   git -C C:\jarvis rev-parse HEAD     # == f59f334…
   git -C C:\jarvis log --oneline -3   # видна Brain-арка
   ```
3. **state/ на месте:** `state/cost_tracking.json`, `state/users.json`, `state/regress_baseline.json`, `state/dev_tasks/` присутствуют и читаются как JSON.
4. **.env:** ключи на месте (`grep -c = .env`), но провалидировать живьём (см. §3 генерации).

---

## Откат/заметки
- Локальная (незашифрованная) копия `C:\jarvis_full_backup_20260708.tar.gz` остаётся на СТАРОМ диске до апгрейда как страховка на случай потери gpg-пароля. После успешного восстановления старый диск не стирать, пока новый не проверен полностью.
- Мёртвые worktree к сносу (для места, отдельно): `C:/jarvis_worktrees/brain-router` (смерджен), `C:/jarvis_worktrees/intent-router` (устарел).
