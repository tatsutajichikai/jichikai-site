from flask import (
    Flask, render_template, request, session,
    redirect, url_for, abort, send_file, flash
)
import os, json, io, re
import cloudinary
import cloudinary.uploader
import cloudinary.api
from cloudinary.utils import cloudinary_url
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["SESSION_PERMANENT"] = False

# Cloudinary設定
cloudinary.config(
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "dyhtmmqnk"),
    api_key    = os.environ.get("CLOUDINARY_API_KEY",    "521635521759147"),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "")
)

KYOGIIN_PREFIXES    = ("/kyogiin",)
ADMIN_PREFIXES      = ("/admin",)
PAGE_ADMIN_PREFIXES = ("/page_admin",)
OPSLOG_PREFIXES     = ("/opslog",)
# ランク2管理者がページ画像編集フォーム（ページ管理者と共通のルート）を使う際に
# 自動ログアウトの対象から外すためのURL
PAGE_EDIT_SHARED_PREFIXES = ("/page_admin/activity", "/page_admin/hero", "/page_admin/news")
# 管理者が資料・議事録の「表示」ボタンを押した時にも管理者セッションを維持する
FILE_VIEW_SHARED_PREFIXES = ("/kyogiin/view", "/kyogiin/raw")
ADMIN_SAFE_PREFIXES = ADMIN_PREFIXES + PAGE_EDIT_SHARED_PREFIXES + FILE_VIEW_SHARED_PREFIXES

@app.before_request
def auto_logout_on_leave():
    path = request.path
    if path.startswith("/static") or path == "/ping" or path == "/favicon.ico" or path.startswith("/.well-known"):
        return

    if session.get("kyogiin_logged_in"):
        if not any(path.startswith(p) for p in KYOGIIN_PREFIXES):
            session.pop("kyogiin_logged_in", None)
            session.pop("kyogiin_name", None)
    if session.get("admin_rank"):
        if not any(path.startswith(p) for p in ADMIN_SAFE_PREFIXES):
            session.pop("admin_rank", None)
            session.pop("admin_name", None)
    if session.get("page_admin_logged_in"):
        if not any(path.startswith(p) for p in PAGE_ADMIN_PREFIXES):
            session.pop("page_admin_logged_in", None)
            session.pop("page_admin_name", None)
    if session.get("opslog_logged_in"):
        if not any(path.startswith(p) for p in OPSLOG_PREFIXES):
            session.pop("opslog_logged_in", None)
    
CONFIG_FILE = "config.json"

# --- ページ管理用: Cloudinary上にJSONを保存・読込する共通関数 -----------------
# config.json（ユーザー/パスワード管理）とは別ファイルとして扱う。
# Render無料プランはディスクが再デプロイ時に消えるため、
# ページ内容(活動タグ詳細・写真リスト等)はCloudinaryに保存して永続化する。

JSON_CONFIG_FOLDER = "jichikai/config"

# ページ内容の読み込みを高速化するための短時間キャッシュ。
# 保存(cloud_json_save)の直後は必ずキャッシュを最新化するので、
# 「追加・削除した直後の画面に反映されない」ということは起きない。
# TTLを過ぎたら通常通りCloudinaryへ再取得しにいく。
_JSON_CACHE = {}
_JSON_CACHE_TTL_SECONDS = 15

def cloud_json_load(name, default):
    """Cloudinaryのrawリソースからjsonを読み込む。存在しなければdefaultを返す。"""
    import time
    now = time.time()
    cached = _JSON_CACHE.get(name)
    if cached is not None and (now - cached[0]) < _JSON_CACHE_TTL_SECONDS:
        return cached[1]

    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "dyhtmmqnk")
    public_id = f"{JSON_CONFIG_FOLDER}/{name}"
    cache_buster = int(now * 1000)
    url = f"https://res.cloudinary.com/{cloud_name}/raw/upload/{public_id}.json?_cb={cache_buster}"
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            _JSON_CACHE[name] = (now, data)
            return data
    except Exception as e:
        print(f"cloud_json_load({name}) failed, using default: {e}")
        return default

def cloud_json_save(name, data):
    """dictをJSON化してCloudinaryにraw保存(上書き)する。"""
    import time
    public_id = f"{JSON_CONFIG_FOLDER}/{name}.json"
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    cloudinary.uploader.upload(
        io.BytesIO(payload),
        public_id=public_id,
        resource_type="raw",
        overwrite=True,
        invalidate=True
    )
    _JSON_CACHE[name] = (time.time(), data)  # 保存内容を即キャッシュに反映

# ---------------------------------------------------------------------------

CONFIG_PUBLIC_ID = "jichikai/config/app_config"

def cloud_config_load():
    """config.jsonをCloudinaryの署名付きURL(authenticated)から読み込む。取得できなければNoneを返す。"""
    try:
        url, _ = cloudinary_url(
            CONFIG_PUBLIC_ID,
            resource_type="raw",
            type="authenticated",
            sign_url=True,
            format="json",
        )
        import urllib.request
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"cloud_config_load failed: {e}")
        return None

def cloud_config_save(data):
    """config.jsonを署名付きURLでしか読めない形でCloudinaryに保存(上書き)する。"""
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    cloudinary.uploader.upload(
        io.BytesIO(payload),
        public_id=CONFIG_PUBLIC_ID,
        resource_type="raw",
        type="authenticated",
        format="json",
        overwrite=True,
        invalidate=True
    )

def log_action(role, name, action, detail=""):
    """操作履歴をCloudinary上に記録する。失敗しても本来の処理は止めない。"""
    try:
        import datetime
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
        entry = {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "role": role,
            "name": name or "",
            "action": action,
            "detail": detail,
        }
        log_data = cloud_json_load("access_log", {"entries": []})
        entries = log_data.get("entries", [])
        entries.append(entry)
        entries = entries[-1000:]  # 直近1000件のみ保持
        cloud_json_save("access_log", {"entries": entries})
    except Exception as e:
        print(f"log_action failed: {e}")

ALLOWED_GIJIROKU = {"pdf"}
BLOCKED_SHIRYO   = {"docx", "xlsx", "pptx", "doc", "xls", "ppt"}
IMAGE_EXTS       = {"jpg", "jpeg", "png", "gif", "webp"}

def safe_public_id(name):
    return name.replace("/", "_").replace("\\", "_")

def strip_month_prefix(name):
    return re.sub(r"^\d{1,2}_", "", name)

def load_config():
    default = {
        "admin2_password_hash": generate_password_hash("admin2-2024"),
        "access_log_password_hash": generate_password_hash("opslog-init-2024"),
        "admin1_users":     {},
        "kyogiin_users":    {},
        "page_admin_users": {},
        "file_meta":        {}
    }

    data = cloud_config_load()
    if data is not None:
        for k, v in default.items():
            data.setdefault(k, v)
        return data

    # Cloudinary側にまだ何もない場合: ローカルに残っているconfig.jsonがあれば
    # 一度だけ読み込み、Cloudinaryへ移行する（以降はCloudinaryのみを使う）
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            try:
                local_data = json.load(f)
                for k, v in default.items():
                    local_data.setdefault(k, v)
                cloud_config_save(local_data)
                return local_data
            except Exception as e:
                print(f"local config.json migration failed: {e}")

    return default

def save_config(cfg):
    cloud_config_save(cfg)

def allowed_gijiroku(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_GIJIROKU

# 自治会基本情報
JICHIKAI = {
    "name": "立田自治会",
    "tagline": "明るく楽しい元気な立田町",
    "description": "私たち立田自治会は、地域の皆さまが安心して暮らせるまちづくりを目指しています。",
#    "email": "jichikai-xxx@example.com",
    "phone": "077-585-2266",
    "address": "滋賀県守山市立田町 1528-4",
    "meeting_day": "毎月第3土曜日 午後19時30分〜",
    "meeting_place": "集落センター",
    "services": [
        {"icon": "🏘️", "title": "地域の安全・防犯", "desc": "年末夜間パトロールや防犯灯の管理を行っています。", "tag_id": "bohan"},
        {"icon": "🌸", "title": "地域イベント", "desc": "立田フェス・敬老会など、年間を通じてイベントを開催しています。", "tag_id": "event"},
        {"icon": "🚨", "title": "防災・災害対策", "desc": "避難訓練の実施や備蓄品の管理など、災害に備えた活動を行っています。", "tag_id": "saigai"},
        {"icon": "♻️", "title": "ごみ・環境美化", "desc": "ごみ収集ルールの周知と、地域の清掃活動を定期的に実施しています。", "tag_id": "gomi"},
        {"icon": "👴", "title": "高齢者・福祉サポート", "desc": "一人暮らしの高齢者への見守り活動や、福祉情報の提供を行っています。", "tag_id": "koreisha"},
        {"icon": "📢", "title": "情報共有・広報", "desc": "回覧板を通じて、地域の最新情報をお届けします。", "tag_id": "joho"},
        {"icon": "🌾", "title": "農業振興組合・農地保全会", "desc": "農業関係の情報を提供します。", "tag_id": "nogyo"},
        {"icon": "🏫", "title": "中洲学区", "desc": "中洲学区の情報や行事など提供します。", "tag_id": "nakasu"},
    ],
    "events": [
        {"month": "4月", "name": "総会"}, {"month": "5月", "name": ""},
        {"month": "6月", "name": "美化運動"}, {"month": "7月", "name": ""},
        {"month": "8月", "name": ""}, {"month": "9月", "name": "総会"},
        {"month": "10月", "name": "立田フェス"}, {"month": "11月", "name": "敬老会・美化運動・防災訓練"},
        {"month": "12月", "name": "夜間パトロール"}, {"month": "1月", "name": ""},
        {"month": "2月", "name": ""}, {"month": "3月", "name": ""},
    ]
}

# 4月開始、3月終了に変更
MONTHS = ["4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月", "1月", "2月", "3月"]

_FILE_LIST_CACHE = {}
_FILE_LIST_CACHE_TTL_SECONDS = 15

def invalidate_file_list_cache(folder_type):
    """資料/議事録アップロード・削除の直後に呼び、一覧キャッシュを即座に無効化する"""
    _FILE_LIST_CACHE.pop(folder_type, None)

def get_files_by_month(folder_type):
    import time
    now = time.time()
    cached = _FILE_LIST_CACHE.get(folder_type)
    if cached is not None and (now - cached[0]) < _FILE_LIST_CACHE_TTL_SECONDS:
        return cached[1]

    result = {m: [] for m in MONTHS}
    prefix = f"jichikai/{folder_type}/"

    for r_type in ["image", "raw"]:
        try:
            res = cloudinary.api.resources(
                type="upload",
                prefix=prefix,
                max_results=500,
                resource_type=r_type
            )
            for r in res.get("resources", []):
                public_id = r["public_id"]
                base_name = public_id.split("/")[-1]
                fmt = r.get("format", "").lower()
                fname = f"{base_name}.{fmt}" if fmt else base_name

                prefix_part = base_name.split("_")[0]
                if prefix_part.isdigit():
                    m_num = int(prefix_part)
                    if 1 <= m_num <= 12:
                        month_key = f"{m_num}月"
                        if month_key in result and fname not in result[month_key]:
                            result[month_key].append(fname)
        except Exception as e:
            print(f"Cloudinary list error ({folder_type}, {r_type}): {e}")

    _FILE_LIST_CACHE[folder_type] = (now, result)
    return result

def get_cloudinary_url(folder_type, fname):
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "dyhtmmqnk")
    if "." in fname:
        base, ext = fname.rsplit(".", 1)
        ext = ext.lower()
    else:
        base, ext = fname, ""
    public_id = f"jichikai/{folder_type}/{base}"
    if ext == "pdf" or ext in IMAGE_EXTS or ext == "":
        url = f"https://res.cloudinary.com/{cloud_name}/image/upload/{public_id}"
        return f"{url}.{ext}" if ext else url
    else:
        return f"https://res.cloudinary.com/{cloud_name}/raw/upload/{public_id}.{ext}"

def get_display_name(fname):
    if "." in fname:
        base, ext = fname.rsplit(".", 1)
        parts = base.split("_", 1)
        display_base = parts[1] if len(parts) > 1 and parts[0].isdigit() else base
        return f"{display_base}.{ext}"
    else:
        parts = fname.split("_", 1)
        return parts[1] if len(parts) > 1 and parts[0].isdigit() else fname

def get_file_meta(cfg, fname):
    meta = cfg.get("file_meta", {}).get(fname, {})
    return {
        "watermark": meta.get("watermark", True),
        "download":  meta.get("download",  False),
        "print":     meta.get("print",     False),
    }

def admin_rank():
    return session.get("admin_rank", 0)

def _page_admin_authorized():
    """
    page_admin系ルートの権限チェック。
    従来の `not session.get("page_admin_logged_in") and admin_rank() < 2` と
    完全に同一の条件（否定形）。挙動は一切変更していない。
    拒否された場合のみ、原因調査のためセッションの状態をログに出す。
    """
    ok = bool(session.get("page_admin_logged_in")) or admin_rank() >= 2
    if not ok:
        print(
            "[page_admin_auth] denied "
            f"path={request.path} "
            f"page_admin_logged_in={session.get('page_admin_logged_in')!r} "
            f"page_admin_name={session.get('page_admin_name')!r} "
            f"admin_rank_in_session={session.get('admin_rank')!r} "
            f"admin_name={session.get('admin_name')!r} "
            f"session_keys={list(session.keys())} "
            f"has_cookie_header={('Cookie' in request.headers)}",
            flush=True,
        )
    return ok

def default_hero_photos():
    return {
        "images": [
            {"url": url_for("static", filename="images/hero_photo1.png"), "alt": "お知らせ画像1"},
            {"url": url_for("static", filename="images/hero_photo2.png"), "alt": "お知らせ画像2"},
            {"url": url_for("static", filename="images/hero_photo3.png"), "alt": "お知らせ画像3"},
        ]
    }

def default_news_items():
    return {"entries": []}

@app.route("/")
def index():
    hero_photos = cloud_json_load("hero_photos", default_hero_photos())
    news_items = cloud_json_load("news_items", default_news_items())
    return render_template("index.html", company=JICHIKAI, hero_photos=hero_photos, news_items=news_items)

@app.route("/kyogiin", methods=["GET", "POST"])
def kyogiin():
    # ログイン後の初期表示を4月に変更
    if session.get("kyogiin_logged_in"):
        return redirect(url_for("kyogiin_files", month="4月"))
    error = None
    if request.method == "POST":
        cfg = load_config()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        users = cfg.get("kyogiin_users", {})
        if name in users and users[name].get("active", True):
            if check_password_hash(users[name]["password_hash"], password):
                session["kyogiin_logged_in"] = True
                session["kyogiin_name"] = name
                log_action("協議員", name, "ログイン")
                return redirect(url_for("kyogiin_files", month="4月"))
        error = "名前またはパスワードが違います"
    return render_template("kyogiin_login.html", company=JICHIKAI, error=error)

@app.route("/kyogiin/logout")
def kyogiin_logout():
    session.pop("kyogiin_logged_in", None)
    session.pop("kyogiin_name", None)
    return redirect(url_for("index"))

@app.route("/kyogiin/files/<month>")
def kyogiin_files(month):
    if not session.get("kyogiin_logged_in"):
        return redirect(url_for("kyogiin"))
    if month not in MONTHS: month = "4月"
    return render_template(
        "kyogiin_files.html",
        company=JICHIKAI, months=MONTHS, current_month=month,
        shiryo=get_files_by_month("shiryo").get(month, []),
        gijiroku=get_files_by_month("gijiroku").get(month, []),
        user_name=session.get("kyogiin_name", ""),
        get_display_name=get_display_name,
    )

@app.route("/kyogiin/change_password", methods=["GET", "POST"])
def kyogiin_change_password():
    if not session.get("kyogiin_logged_in"):
        return redirect(url_for("kyogiin"))
    user_name = session.get("kyogiin_name", "")
    msg = None
    if request.method == "POST":
        cfg = load_config()
        cur_pw = request.form.get("current_password", "").strip()
        new_pw = request.form.get("new_password", "").strip()
        conf_pw = request.form.get("confirm_password", "").strip()
        if not check_password_hash(cfg["kyogiin_users"][user_name]["password_hash"], cur_pw):
            msg = ("danger", "現在のパスワードが違います")
        elif len(new_pw) < 4:
            msg = ("danger", "新しいパスワードは4文字以上で入力してください")
        elif new_pw != conf_pw:
            msg = ("danger", "確認用パスワードが一致しません")
        else:
            cfg["kyogiin_users"][user_name]["password_hash"] = generate_password_hash(new_pw)
            save_config(cfg)
            msg = ("success", "パスワードを変更しました")
    return render_template("kyogiin_change_password.html", company=JICHIKAI, user_name=user_name, msg=msg)

@app.route("/kyogiin/view/<file_type>/<path:filename>")
def kyogiin_view_file(file_type, filename):
    if not session.get("kyogiin_logged_in") and admin_rank() < 1:
        return redirect(url_for("kyogiin"))
    if file_type not in ("shiryo", "gijiroku"): abort(404)
    safe = os.path.basename(filename)
    if session.get("kyogiin_logged_in"):
        log_action("協議員", session.get("kyogiin_name", ""), "閲覧", f"{file_type}: {get_display_name(safe)}")
    elif admin_rank() >= 1:
        log_action("管理者", session.get("admin_name", ""), "閲覧", f"{file_type}: {get_display_name(safe)}")

    # 透かしに表示する名前: 協議員はご自身の名前、ランク1管理者もご自身の名前、
    # ランク2管理者は名前を持たないため空欄（＝日付のみ表示）とする
    if session.get("kyogiin_logged_in"):
        viewer_name = session.get("kyogiin_name", "")
    elif admin_rank() == 1:
        viewer_name = session.get("admin_name", "")
    else:
        viewer_name = ""

    cfg = load_config()
    meta = get_file_meta(cfg, safe) if file_type == "shiryo" else {"watermark": True, "download": False, "print": False}
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    file_url = url_for("kyogiin_raw_file", file_type=file_type, filename=safe)

    # 戻り先の月を判定するための数値取得
    prefix_part = safe.split("_")[0]
    back_month = f"{int(prefix_part)}月" if prefix_part.isdigit() else "4月"

    return render_template(
        "kyogiin_viewer.html",
        company=JICHIKAI,
        filename=safe,
        display_name=get_display_name(safe),
        user_name=viewer_name,
        file_url=file_url,
        file_url_abs=request.host_url.rstrip("/") + file_url,
        file_ext=ext,
        watermark=meta["watermark"],
        allow_download=meta["download"],
        allow_print=meta["print"],
        is_pdf=(ext == "pdf"),
        file_type=file_type,
        back_month=back_month
    )

@app.route("/kyogiin/raw/<file_type>/<path:filename>")
def kyogiin_raw_file(file_type, filename):
    if not session.get("kyogiin_logged_in") and admin_rank() < 1: abort(403)
    safe = os.path.basename(filename)
    if file_type == "shiryo":
        cfg = load_config()
        meta = get_file_meta(cfg, safe)
        if request.args.get("dl") == "1" and not meta["download"]: abort(403)
    return redirect(get_cloudinary_url(file_type, safe))

@app.route("/admin/rank1", methods=["GET", "POST"])
def admin1_login():
    if admin_rank() >= 1: return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        cfg = load_config()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        a1 = cfg.get("admin1_users", {})
        if name in a1 and a1[name].get("active", True):
            if check_password_hash(a1[name]["password_hash"], password):
                session["admin_rank"] = 1
                session["admin_name"] = name
                log_action("管理者", name, "ログイン")
                return redirect(url_for("admin_dashboard"))
        error = "名前またはパスワードが違います"
    return render_template("admin1_login.html", company=JICHIKAI, error=error)

@app.route("/admin/rank1/change_password", methods=["GET", "POST"])
def admin1_change_password():
    if admin_rank() != 1:
        return redirect(url_for("admin1_login"))
    admin_name = session.get("admin_name", "")
    msg = None
    if request.method == "POST":
        cfg = load_config()
        cur_pw = request.form.get("current_password", "").strip()
        new_pw = request.form.get("new_password", "").strip()
        conf_pw = request.form.get("confirm_password", "").strip()
        admin_info = cfg.get("admin1_users", {}).get(admin_name)
        if not admin_info or not check_password_hash(admin_info["password_hash"], cur_pw):
            msg = ("danger", "現在のパスワードが違います")
        elif len(new_pw) < 4:
            msg = ("danger", "新しいパスワードは4文字以上で入力してください")
        elif new_pw != conf_pw:
            msg = ("danger", "確認用パスワードが一致しません")
        else:
            cfg["admin1_users"][admin_name]["password_hash"] = generate_password_hash(new_pw)
            save_config(cfg)
            msg = ("success", "パスワードを変更しました")
    return render_template("admin1_change_password.html", company=JICHIKAI, admin_name=admin_name, msg=msg)

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if admin_rank() >= 1: return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        cfg = load_config()
        password = request.form.get("password", "").strip()
        if check_password_hash(cfg["admin2_password_hash"], password):
            session["admin_rank"] = 2
            session["admin_name"] = "管理者"
            log_action("管理者", "管理者", "ログイン")
            return redirect(url_for("admin_dashboard"))
        error = "パスワードが違います"
    return render_template("admin_login.html", company=JICHIKAI, error=error)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_rank", None)
    session.pop("admin_name", None)
    return redirect(url_for("index"))

@app.route("/admin/dashboard", methods=["GET", "POST"])
def admin_dashboard():
    if admin_rank() < 1: return redirect(url_for("admin_login"))
    cfg = load_config()
    msg = None

    page_tag_contents = {}
    page_hero_photos = {}
    page_news_items = {"entries": []}
    if admin_rank() == 2:
        for t in ACTIVITY_TAGS:
            page_tag_contents[t["id"]] = cloud_json_load(
                f"activity_{t['id']}", default_activity_content(t["id"], t["title"])
            )
        page_hero_photos = cloud_json_load("hero_photos", default_hero_photos())
        page_news_items = cloud_json_load("news_items", default_news_items())

    if request.method == "POST":
        action = request.form.get("action")
        if action == "upload_shiryo":
            month = request.form.get("month", "4月")
            file = request.files.get("file")
            watermark   = request.form.get("watermark") == "1"
            download    = request.form.get("download")  == "1"
            allow_print = request.form.get("print")     == "1"
            if not file or file.filename == "":
                msg = ("danger", "ファイル未選択")
            elif file.filename.rsplit(".", 1)[-1].lower() in BLOCKED_SHIRYO:
                msg = ("danger", "PDF化してください")
            else:
                m_match = re.search(r"(\d+)", month)
                month_num = int(m_match.group(1)) if m_match else 4
                original_filename = file.filename
                ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
                base_name = strip_month_prefix(
                    original_filename.rsplit(".", 1)[0] if "." in original_filename else original_filename
                )
                public_id_base = "{:02d}_{}".format(month_num, base_name)
                resource_type  = "image" if (ext in IMAGE_EXTS or ext == "pdf") else "raw"
                save_name = f"{public_id_base}.{ext}" if ext else public_id_base

                try:
                    cloudinary.uploader.upload(
                        file,
                        public_id=public_id_base,
                        folder="jichikai/shiryo",
                        resource_type=resource_type,
                        use_filename=True,
                        unique_filename=False,
                        overwrite=True
                    )
                    cfg.setdefault("file_meta", {})[save_name] = {
                        "watermark": watermark,
                        "download":  download,
                        "print":     allow_print
                    }
                    save_config(cfg)
                    invalidate_file_list_cache("shiryo")
                    msg = ("success", f"{month}に資料「{original_filename}」をアップロードしました")
                    log_action("管理者", session.get("admin_name", ""), "資料アップロード", f"{month}: {original_filename}")
                except Exception as e:
                    msg = ("danger", f"失敗: {e}")

        elif action == "upload_gijiroku":

            month = request.form.get("month", "4月")
            file = request.files.get("file")
            if not file or not allowed_gijiroku(file.filename):
                msg = ("danger", "PDFのみ")
            else:
                m_match = re.search(r"(\d+)", month)
                month_num = int(m_match.group(1)) if m_match else 4
                original_filename = file.filename
                base_name = strip_month_prefix(original_filename.rsplit(".", 1)[0])
                try:
                    cloudinary.uploader.upload(
                        file,
                        public_id="{:02d}_{}".format(month_num, base_name),
                        folder="jichikai/gijiroku",
                        resource_type="image",
                        use_filename=True,
                        unique_filename=False,
                        overwrite=True
                    )
                    invalidate_file_list_cache("gijiroku")
                    msg = ("success", f"{month}に議事録「{original_filename}」をアップロードしました")
                    log_action("管理者", session.get("admin_name", ""), "議事録アップロード", f"{month}: {original_filename}")
                except Exception as e:
                    msg = ("danger", f"失敗: {e}")

        elif action == "delete_shiryo":
            fname = request.form.get("filename", "")
            base  = fname.rsplit(".", 1)[0] if "." in fname else fname
            ext   = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            r_type = "image" if (ext in IMAGE_EXTS or ext == "pdf") else "raw"
            try:
                cloudinary.uploader.destroy(f"jichikai/shiryo/{base}", resource_type=r_type)
                cfg.get("file_meta", {}).pop(fname, None)
                save_config(cfg)
                invalidate_file_list_cache("shiryo")
                msg = ("success", f"「{get_display_name(fname)}」を削除しました")
                log_action("管理者", session.get("admin_name", ""), "資料削除", get_display_name(fname))
            except Exception as e:
                msg = ("danger", f"失敗: {e}")

        elif action == "delete_gijiroku":
            fname = request.form.get("filename", "")
            base  = fname.rsplit(".", 1)[0] if "." in fname else fname
            try:
                cloudinary.uploader.destroy(f"jichikai/gijiroku/{base}", resource_type="image")
                save_config(cfg)
                invalidate_file_list_cache("gijiroku")
                msg = ("success", f"「{get_display_name(fname)}」を削除しました")
                log_action("管理者", session.get("admin_name", ""), "議事録削除", get_display_name(fname))
            except Exception as e:
                msg = ("danger", f"失敗: {e}")

        elif admin_rank() == 2:
            if action == "add_kyogiin":
                name    = request.form.get("new_name", "")
                pw      = request.form.get("new_password", "")
                conf_pw = request.form.get("confirm_password", "")
                if name and pw == conf_pw:
                    cfg["kyogiin_users"][name] = {"password_hash": generate_password_hash(pw), "active": True}
                    save_config(cfg)
                    msg = ("success", "追加成功")
            elif action == "toggle_kyogiin":
                name = request.form.get("user_name", "")
                if name in cfg["kyogiin_users"]:
                    cfg["kyogiin_users"][name]["active"] = not cfg["kyogiin_users"][name].get("active", True)
                    save_config(cfg)
                    msg = ("success", "切り替え成功")
            elif action == "delete_kyogiin":
                name = request.form.get("user_name", "")
                if name in cfg["kyogiin_users"]:
                    del cfg["kyogiin_users"][name]
                    save_config(cfg)
                    msg = ("success", "削除成功")
            elif action == "add_admin1":
                name    = request.form.get("new_name", "")
                pw      = request.form.get("new_password", "")
                conf_pw = request.form.get("confirm_password", "")
                if name and pw == conf_pw:
                    cfg["admin1_users"][name] = {"password_hash": generate_password_hash(pw), "active": True}
                    save_config(cfg)
                    msg = ("success", "ランク1管理者追加成功")
            elif action == "toggle_admin1":
                name = request.form.get("user_name", "")
                if name in cfg["admin1_users"]:
                    cfg["admin1_users"][name]["active"] = not cfg["admin1_users"][name].get("active", True)
                    save_config(cfg)
                    msg = ("success", "切り替え成功")
            elif action == "delete_admin1":
                name = request.form.get("user_name", "")
                if name in cfg["admin1_users"]:
                    del cfg["admin1_users"][name]
                    save_config(cfg)
                    msg = ("success", "削除成功")
            elif action == "add_page_admin":
                name    = request.form.get("new_name", "")
                pw      = request.form.get("new_password", "")
                conf_pw = request.form.get("confirm_password", "")
                if name and pw == conf_pw:
                    cfg.setdefault("page_admin_users", {})[name] = {"password_hash": generate_password_hash(pw), "active": True}
                    save_config(cfg)
                    msg = ("success", "ページ管理者を追加しました")
            elif action == "toggle_page_admin":
                name = request.form.get("user_name", "")
                if name in cfg.get("page_admin_users", {}):
                    cfg["page_admin_users"][name]["active"] = not cfg["page_admin_users"][name].get("active", True)
                    save_config(cfg)
                    msg = ("success", "切り替え成功")
            elif action == "delete_page_admin":
                name = request.form.get("user_name", "")
                if name in cfg.get("page_admin_users", {}):
                    del cfg["page_admin_users"][name]
                    save_config(cfg)
                    msg = ("success", "削除成功")
            elif action == "change_access_log_pw":
                cur_pw = request.form.get("current_password", "").strip()
                new_pw = request.form.get("new_password", "").strip()
                conf_pw = request.form.get("confirm_password", "").strip()
                if not check_password_hash(cfg.get("access_log_password_hash", ""), cur_pw):
                    msg = ("danger", "現在のパスワードが違います")
                elif len(new_pw) < 4:
                    msg = ("danger", "新しいパスワードは4文字以上で入力してください")
                elif new_pw != conf_pw:
                    msg = ("danger", "確認用パスワードが一致しません")
                else:
                    cfg["access_log_password_hash"] = generate_password_hash(new_pw)
                    save_config(cfg)
                    msg = ("success", "操作履歴パスワードを変更しました")

        cfg = load_config()

    return render_template(
        "admin_dashboard.html",
        company=JICHIKAI,
        months=MONTHS,
        shiryo_by_month=get_files_by_month("shiryo"),
        gijiroku_by_month=get_files_by_month("gijiroku"),
        kyogiin_users=cfg.get("kyogiin_users", {}),
        admin1_users=cfg.get("admin1_users", {}),
        page_admin_users=cfg.get("page_admin_users", {}),
        file_meta=cfg.get("file_meta", {}),
        admin_rank=admin_rank(),
        admin_name=session.get("admin_name", ""),
        msg=msg,
        get_display_name=get_display_name,
        activity_tags=ACTIVITY_TAGS,
        page_tag_contents=page_tag_contents,
        page_hero_photos=page_hero_photos,
        news_items=page_news_items,
    )

@app.route("/admin/download_config")
def admin_download_config():
    if admin_rank() < 2: return redirect(url_for("admin_login"))
    cfg = load_config()
    buf = io.BytesIO(json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8"))
    return send_file(buf, as_attachment=True, download_name="config_backup.json", mimetype="application/json")

@app.route("/admin/upload_config", methods=["POST"])
def admin_upload_config():
    if admin_rank() < 2: return redirect(url_for("admin_login"))
    file = request.files.get("config_file")
    if file:
        try: save_config(json.load(file))
        except: pass
    return redirect(url_for("admin_dashboard"))

# --- 活動タグ詳細ページ（ページ管理画面から中身を差し替え可能） -----------------
ACTIVITY_TAGS = [
    {"id": "bohan",    "title": "地域の安全・防犯",     "icon": "🏘️"},
    {"id": "event",    "title": "地域イベント",         "icon": "🌸"},
    {"id": "saigai",   "title": "防災・災害対策",       "icon": "🚨"},
    {"id": "gomi",     "title": "ごみ・環境美化",       "icon": "♻️"},
    {"id": "koreisha", "title": "高齢者・福祉サポート", "icon": "👴"},
    {"id": "joho",     "title": "情報共有・サポート",   "icon": "📢"},
    {"id": "nogyo",    "title": "農業振興組合・農地保全会", "icon": "🌾"},
    {"id": "nakasu",   "title": "中洲学区",             "icon": "🏫"},
]
ACTIVITY_TAG_IDS = {t["id"] for t in ACTIVITY_TAGS}

def default_activity_content(tag_id, title):
    images = []
    if tag_id == "event":
        # 既存の地域イベントページの画像をそのまま初期値として維持
        images = [{"url": url_for("static", filename="images/月の観察会.png"), "name": "月の観察会.png"}]
    return {"title": title, "body": "", "images": images}

@app.route("/activity/<tag_id>")
def activity_detail(tag_id):
    if tag_id not in ACTIVITY_TAG_IDS:
        abort(404)
    tag_title = next(t["title"] for t in ACTIVITY_TAGS if t["id"] == tag_id)
    content = cloud_json_load(f"activity_{tag_id}", default_activity_content(tag_id, tag_title))
    return render_template("activity_detail.html", company=JICHIKAI, tag_id=tag_id, content=content)

# --- ページ管理者（活動タグ・トップ写真の編集用） ------------------------------
@app.route("/page_admin/login", methods=["GET", "POST"])
def page_admin_login():
    if session.get("page_admin_logged_in"):
        return redirect(url_for("page_admin_dashboard"))
    error = None
    if request.method == "POST":
        cfg = load_config()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        users = cfg.get("page_admin_users", {})
        if name in users and users[name].get("active", True):
            if check_password_hash(users[name]["password_hash"], password):
                session["page_admin_logged_in"] = True
                session["page_admin_name"] = name
                log_action("ページ管理者", name, "ログイン")
                return redirect(url_for("page_admin_dashboard"))
        error = "名前またはパスワードが違います"
    return render_template("page_admin_login.html", company=JICHIKAI, error=error)

@app.route("/page_admin/logout")
def page_admin_logout():
    session.pop("page_admin_logged_in", None)
    session.pop("page_admin_name", None)
    return redirect(url_for("index"))

@app.route("/page_admin/change_password", methods=["GET", "POST"])
def page_admin_change_password():
    if not session.get("page_admin_logged_in"):
        return redirect(url_for("page_admin_login"))
    user_name = session.get("page_admin_name", "")
    msg = None
    if request.method == "POST":
        cfg = load_config()
        cur_pw = request.form.get("current_password", "").strip()
        new_pw = request.form.get("new_password", "").strip()
        conf_pw = request.form.get("confirm_password", "").strip()
        if not check_password_hash(cfg["page_admin_users"][user_name]["password_hash"], cur_pw):
            msg = ("danger", "現在のパスワードが違います")
        elif len(new_pw) < 4:
            msg = ("danger", "新しいパスワードは4文字以上で入力してください")
        elif new_pw != conf_pw:
            msg = ("danger", "確認用パスワードが一致しません")
        else:
            cfg["page_admin_users"][user_name]["password_hash"] = generate_password_hash(new_pw)
            save_config(cfg)
            msg = ("success", "パスワードを変更しました")
    return render_template("page_admin_change_password.html", company=JICHIKAI, user_name=user_name, msg=msg)

def _page_editor_actor():
    """ページ画像編集ルートの操作者を判定する（フォームのorigin値で明示的に区別）"""
    origin = request.form.get("origin", "")
    if origin == "page_admin":
        return "ページ管理者", session.get("page_admin_name", "")
    return "管理者", session.get("admin_name", "")

def _page_editor_redirect():
    """画像編集後の戻り先: フォームのorigin値に基づいて判定する"""
    origin = request.form.get("origin", "")
    if origin == "page_admin":
        return redirect(url_for("page_admin_dashboard"))
    return redirect(url_for("admin_dashboard"))

@app.route("/page_admin/dashboard")
def page_admin_dashboard():

    if not session.get("page_admin_logged_in"):
        return redirect(url_for("page_admin_login"))
    tag_contents = {}
    for t in ACTIVITY_TAGS:
        tag_contents[t["id"]] = cloud_json_load(
            f"activity_{t['id']}", default_activity_content(t["id"], t["title"])
        )
    hero_photos = cloud_json_load("hero_photos", default_hero_photos())
    news_items = cloud_json_load("news_items", default_news_items())
    return render_template(
        "page_admin_dashboard.html",
        company=JICHIKAI,
        activity_tags=ACTIVITY_TAGS,
        tag_contents=tag_contents,
        hero_photos=hero_photos,
        news_items=news_items,
    )

# -------------------------------------------------------------------------------

@app.route("/page_admin/activity/<tag_id>/save", methods=["POST"])
def page_admin_activity_save(tag_id):
    if not _page_admin_authorized():
        return redirect(url_for("page_admin_login"))
    if tag_id not in ACTIVITY_TAG_IDS:
        abort(404)

    tag_title = next(t["title"] for t in ACTIVITY_TAGS if t["id"] == tag_id)
    content = cloud_json_load(f"activity_{tag_id}", default_activity_content(tag_id, tag_title))

    content["body"] = request.form.get("body", "").strip()
    cloud_json_save(f"activity_{tag_id}", content)

    role, name = _page_editor_actor()
    log_action(role, name, "活動タグ本文保存", tag_title)
    flash(f"「{tag_title}」の本文を保存しました", "success")
    return _page_editor_redirect()

@app.route("/page_admin/activity/<tag_id>/upload_image", methods=["POST"])
def page_admin_activity_upload_image(tag_id):
    if not _page_admin_authorized():
        return redirect(url_for("page_admin_login"))
    if tag_id not in ACTIVITY_TAG_IDS:
        abort(404)

    file = request.files.get("image")
    if file and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext in IMAGE_EXTS:
            tag_title = next(t["title"] for t in ACTIVITY_TAGS if t["id"] == tag_id)
            content = cloud_json_load(f"activity_{tag_id}", default_activity_content(tag_id, tag_title))

            import time
            public_id = f"jichikai/activity/{tag_id}_{int(time.time())}"
            result = cloudinary.uploader.upload(
                file,
                public_id=public_id,
                resource_type="image",
                overwrite=True
            )
            content.setdefault("images", []).append({"url": result["secure_url"], "name": file.filename})
            cloud_json_save(f"activity_{tag_id}", content)

            role, name = _page_editor_actor()
            log_action(role, name, "活動タグ画像追加", f"{tag_title}: {file.filename}")
            flash(f"「{tag_title}」に画像を追加しました", "success")
        else:
            flash("対応していない画像形式です", "danger")

    return _page_editor_redirect()

@app.route("/page_admin/activity/<tag_id>/delete_image", methods=["POST"])
def page_admin_activity_delete_image(tag_id):
    if not _page_admin_authorized():
        return redirect(url_for("page_admin_login"))
    if tag_id not in ACTIVITY_TAG_IDS:
        abort(404)

    img_url = request.form.get("img_url", "")
    tag_title = next(t["title"] for t in ACTIVITY_TAGS if t["id"] == tag_id)
    content = cloud_json_load(f"activity_{tag_id}", default_activity_content(tag_id, tag_title))
    deleted_name = next((i.get("name", "") for i in content.get("images", []) if i.get("url") == img_url), "")
    content["images"] = [i for i in content.get("images", []) if i.get("url") != img_url]
    cloud_json_save(f"activity_{tag_id}", content)

    role, name = _page_editor_actor()
    log_action(role, name, "活動タグ画像削除", f"{tag_title}: {deleted_name}")
    flash(f"「{tag_title}」の画像を削除しました", "success")
    return _page_editor_redirect()

@app.route("/page_admin/hero/upload", methods=["POST"])
def page_admin_hero_upload():
    if not _page_admin_authorized():
        return redirect(url_for("page_admin_login"))

    file = request.files.get("image")
    alt_text = request.form.get("alt", "").strip() or "お知らせ画像"
    if file and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext in IMAGE_EXTS:
            hero_photos = cloud_json_load("hero_photos", default_hero_photos())

            import time
            public_id = f"jichikai/hero/photo_{int(time.time())}"
            result = cloudinary.uploader.upload(
                file,
                public_id=public_id,
                resource_type="image",
                overwrite=True
            )
            hero_photos.setdefault("images", []).append({"url": result["secure_url"], "alt": alt_text})
            cloud_json_save("hero_photos", hero_photos)

            role, name = _page_editor_actor()
            log_action(role, name, "トップ写真追加", alt_text)
            flash("トップページの写真を追加しました", "success")
        else:
            flash("対応していない画像形式です", "danger")

    return _page_editor_redirect()

@app.route("/page_admin/hero/delete", methods=["POST"])
def page_admin_hero_delete():
    if not _page_admin_authorized():
        return redirect(url_for("page_admin_login"))
    
    img_url = request.form.get("img_url", "")
    hero_photos = cloud_json_load("hero_photos", default_hero_photos())
    deleted_alt = next((p.get("alt", "") for p in hero_photos.get("images", []) if p.get("url") == img_url), "")
    hero_photos["images"] = [p for p in hero_photos.get("images", []) if p.get("url") != img_url]
    cloud_json_save("hero_photos", hero_photos)

    role, name = _page_editor_actor()
    log_action(role, name, "トップ写真削除", deleted_alt)
    flash("トップページの写真を削除しました", "success")
    return _page_editor_redirect()

@app.route("/page_admin/news/add", methods=["POST"])
def page_admin_news_add():
    if not _page_admin_authorized():
        return redirect(url_for("page_admin_login"))
    
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if title:
        import datetime, time
        news = cloud_json_load("news_items", default_news_items())
        entry = {
            "id": str(int(time.time() * 1000)),
            "date": (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime("%Y-%m-%d"),
            "title": title,
            "body": body,
        }
        entries = news.get("entries", [])
        entries.insert(0, entry)
        entries = entries[:50]
        news["entries"] = entries
        cloud_json_save("news_items", news)

        role, name = _page_editor_actor()
        log_action(role, name, "新着情報追加", title)
        flash(f"新着情報「{title}」を追加しました", "success")

    return _page_editor_redirect()

@app.route("/page_admin/news/delete", methods=["POST"])
def page_admin_news_delete():
    if not _page_admin_authorized():
        return redirect(url_for("page_admin_login"))
    
    entry_id = request.form.get("entry_id", "")
    news = cloud_json_load("news_items", default_news_items())
    deleted_title = next((e.get("title", "") for e in news.get("entries", []) if e.get("id") == entry_id), "")
    news["entries"] = [e for e in news.get("entries", []) if e.get("id") != entry_id]
    cloud_json_save("news_items", news)

    role, name = _page_editor_actor()
    log_action(role, name, "新着情報削除", deleted_title)
    flash(f"新着情報「{deleted_title}」を削除しました", "success")
    return _page_editor_redirect()

# --- 操作履歴（アイコン・リンクは一切設置しない。URLを直接開いてアクセスする） ------
@app.route("/opslog/login", methods=["GET", "POST"])
def opslog_login():
    if session.get("opslog_logged_in"):
        return redirect(url_for("opslog_view"))
    error = None
    if request.method == "POST":
        cfg = load_config()
        password = request.form.get("password", "").strip()
        if check_password_hash(cfg.get("access_log_password_hash", ""), password):
            session["opslog_logged_in"] = True
            return redirect(url_for("opslog_view"))
        error = "パスワードが違います"
    return render_template("opslog_login.html", company=JICHIKAI, error=error)

@app.route("/opslog/logout")
def opslog_logout():
    session.pop("opslog_logged_in", None)
    return redirect(url_for("index"))

@app.route("/opslog")
def opslog_view():
    if not session.get("opslog_logged_in"):
        return redirect(url_for("opslog_login"))
    log_data = cloud_json_load("access_log", {"entries": []})
    entries = list(reversed(log_data.get("entries", [])))
    return render_template("opslog_view.html", company=JICHIKAI, entries=entries)

@app.route("/opslog/change_password", methods=["GET", "POST"])
def opslog_change_password():
    if not session.get("opslog_logged_in"):
        return redirect(url_for("opslog_login"))
    msg = None
    if request.method == "POST":
        cfg = load_config()
        cur_pw = request.form.get("current_password", "").strip()
        new_pw = request.form.get("new_password", "").strip()
        conf_pw = request.form.get("confirm_password", "").strip()
        if not check_password_hash(cfg.get("access_log_password_hash", ""), cur_pw):
            msg = ("danger", "現在のパスワードが違います")
        elif len(new_pw) < 4:
            msg = ("danger", "新しいパスワードは4文字以上で入力してください")
        elif new_pw != conf_pw:
            msg = ("danger", "確認用パスワードが一致しません")
        else:
            cfg["access_log_password_hash"] = generate_password_hash(new_pw)
            save_config(cfg)
            msg = ("success", "パスワードを変更しました")
    return render_template("opslog_change_password.html", company=JICHIKAI, msg=msg)
# -------------------------------------------------------------------------------

@app.route("/opslog/download_csv")
def opslog_download_csv():
    if not session.get("opslog_logged_in"):
        return redirect(url_for("opslog_login"))
    log_data = cloud_json_load("access_log", {"entries": []})
    entries = list(reversed(log_data.get("entries", [])))

    import csv
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["日時", "区分", "名前", "操作", "詳細"])
    for e in entries:
        writer.writerow([e.get("time", ""), e.get("role", ""), e.get("name", ""), e.get("action", ""), e.get("detail", "")])

    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")  # Excel対策でBOM付きUTF-8
    return send_file(
        io.BytesIO(csv_bytes),
        as_attachment=True,
        download_name="operation_log.csv",
        mimetype="text/csv"
    )

@app.route('/event/chiiki')
def event_chiiki():
    # 旧URL: 新しい汎用ページへリダイレクト（既存のブックマーク・リンク対策）
    return redirect(url_for("activity_detail", tag_id="event"))

@app.route("/ping")
def ping(): return "pong", 200

@app.route("/sitemap.xml")
def sitemap():
    from flask import Response
    base = "https://jichikai-site-surx.onrender.com"
    urls = [f"{base}/"] + [f"{base}/activity/{t['id']}" for t in ACTIVITY_TAGS]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u in urls:
        xml += f"  <url><loc>{u}</loc></url>\n"
    xml += "</urlset>"
    return Response(xml, mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    from flask import Response
    lines = [
        "User-agent: *",
        "Disallow: /kyogiin",
        "Disallow: /admin",
        "Disallow: /page_admin",
        "Disallow: /opslog",
        f"Sitemap: https://jichikai-site-surx.onrender.com/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)