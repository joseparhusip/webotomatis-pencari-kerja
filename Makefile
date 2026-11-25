# Shortcut Docker & aplikasi Webotomatis.
#
#   make              -> daftar perintah
#   make up           -> nyalakan semua (web di http://localhost:8030)
#   make logs s=worker
#
# Semua perintah jalan dari PowerShell, cmd, maupun Git Bash (tanpa sintaks khusus shell).

COMPOSE = docker compose
EXEC    = $(COMPOSE) exec
s       ?=
src     ?=
name    ?=
file    ?= backup-db.sql
url     ?= http://localhost:8030

.DEFAULT_GOAL := help
.PHONY: help up build rebuild down stop start restart restart-web restart-worker ps logs logs-web logs-worker \
        migrate migration migrate-down migrate-status test test-x shell shell-worker psql redis \
        scrape scrape-now rescore details audit ig-check backup restore pull clean status

help:
	$(info Webotomatis - daftar shortcut. Contoh: make up)
	$(info --------------------------------------------------------------------)
	$(info DOCKER)
	$(info   make up              nyalakan semua container: web, worker, postgres, redis)
	$(info   make build           build ulang image lalu nyalakan - setelah ubah requirements/Dockerfile)
	$(info   make rebuild         build ulang TANPA cache lalu nyalakan)
	$(info   make down            matikan dan hapus container - data database tetap AMAN di volume)
	$(info   make stop            matikan container saja, tidak dihapus)
	$(info   make start           nyalakan lagi container yang di-stop)
	$(info   make restart         restart web dan worker - memuat ulang kode)
	$(info   make restart-web     restart web saja)
	$(info   make restart-worker  restart worker saja - wajib setelah ubah kode worker/scraping)
	$(info   make ps              status container)
	$(info   make status          status container dan cek web merespons)
	$(info   make logs            log semua service - Ctrl+C untuk keluar)
	$(info   make logs s=worker   log satu service: web, worker, postgres, redis)
	$(info   make logs-web        log web)
	$(info   make logs-worker     log worker - scraping dan kesimpulan AI CV)
	$(info   make clean           down dan hapus container yatim - data database tetap aman)
	$(info   make pull            perbarui image postgres dan redis)
	$(info DATABASE)
	$(info   make migrate         jalankan migrasi: alembic upgrade head)
	$(info   make migration name=tambah_kolom   buat file migrasi baru otomatis)
	$(info   make migrate-down    batalkan 1 migrasi terakhir)
	$(info   make migrate-status  versi migrasi sekarang)
	$(info   make psql            masuk PostgreSQL - keluar dengan Ctrl+D)
	$(info   make backup          simpan database ke backup-db.sql - atau file=nama.sql)
	$(info   make restore file=backup-db.sql   pulihkan database dari file)
	$(info   make redis           masuk redis-cli)
	$(info APLIKASI)
	$(info   make scrape          jalankan scraping lewat worker - sama dengan tombol di web)
	$(info   make scrape-now src=jobstreet   scraping langsung sumber tertentu tanpa worker)
	$(info   make rescore         hitung ulang kategori dan skor semua lowongan)
	$(info   make details         lengkapi halaman detail lowongan yang belum lengkap)
	$(info   make audit           cek kelengkapan JobStreet dibanding database)
	$(info   make ig-check        cek konfigurasi Instagram/Meta)
	$(info PENGEMBANGAN)
	$(info   make test            jalankan semua tes)
	$(info   make test-x          tes, berhenti di kegagalan pertama)
	$(info   make shell           terminal di container web - keluar dengan exit)
	$(info   make shell-worker    terminal di container worker)
	$(info --------------------------------------------------------------------)
	$(info Web: $(url))
	@:

# ---------- Docker ----------
up:
	$(COMPOSE) up -d
	@echo Web: $(url)

build:
	$(COMPOSE) up -d --build

rebuild:
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

stop:
	$(COMPOSE) stop

start:
	$(COMPOSE) start

restart:
	$(COMPOSE) restart web worker

restart-web:
	$(COMPOSE) restart web

restart-worker:
	$(COMPOSE) restart worker

ps:
	$(COMPOSE) ps

status:
	$(COMPOSE) ps
	curl -s -o NUL -w "web $(url) - HTTP %{http_code}\n" $(url)/jobs

logs:
	$(COMPOSE) logs -f --tail 100 $(s)

logs-web:
	$(COMPOSE) logs -f --tail 100 web

logs-worker:
	$(COMPOSE) logs -f --tail 100 worker

clean:
	$(COMPOSE) down --remove-orphans

pull:
	$(COMPOSE) pull postgres redis

# ---------- Database ----------
migrate:
	$(EXEC) web alembic upgrade head

migration:
ifeq ($(strip $(name)),)
	$(error Beri nama migrasi, contoh: make migration name=tambah_kolom_x)
endif
	$(EXEC) web alembic revision --autogenerate -m "$(name)"

migrate-down:
	$(EXEC) web alembic downgrade -1

migrate-status:
	$(EXEC) web alembic current

psql:
	$(EXEC) postgres psql -U jobs -d jobs

backup:
	$(EXEC) -T postgres pg_isready -U jobs -q
	$(EXEC) -T postgres pg_dump -U jobs -d jobs --clean --if-exists > $(file)
	@echo Database tersimpan di $(file)

restore:
	$(EXEC) -T postgres pg_isready -U jobs -q
	$(EXEC) -T postgres psql -U jobs -d jobs -q < $(file)
	@echo Database dipulihkan dari $(file)

redis:
	$(EXEC) redis redis-cli

# ---------- Aplikasi ----------
scrape:
	curl -s -X POST -H "HX-Request: true" $(url)/scrape
	@echo Progres scraping: buka $(url)/settings atau jalankan make logs-worker

scrape-now:
	$(EXEC) web python -m app.scripts.scrape $(src)

rescore:
	$(EXEC) web python -m app.scripts.rescore

details:
	$(EXEC) web python -m app.scripts.backfill_details

audit:
	$(EXEC) web python -m app.scripts.audit_jobstreet

ig-check:
	$(EXEC) web python -m app.scripts.ig_check

# ---------- Pengembangan ----------
test:
	$(EXEC) -T web python -m pytest -q

test-x:
	$(EXEC) -T web python -m pytest -q -x

shell:
	$(EXEC) web sh

shell-worker:
	$(EXEC) worker sh
