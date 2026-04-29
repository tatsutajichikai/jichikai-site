from flask import (
    Flask, render_template, request, session,
    redirect, url_for, abort, send_file
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

cloudinary.config(
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "dyhtmmqnk"),
    api_key    = os.environ.get("CLOUDINARY_API_KEY",    "521635521759147"),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "")
)

KYOGIIN_PREFIXES = ("/kyogiin",)
ADMIN_PREFIXES   = ("/admin",)

@app.before_request
def auto_logout_on_leave():
    path = request.path
    if path.startswith("/static") or path == "/ping":
        return
    if session.get("kyogiin_logged_in"):
        if not any(path.startswith(p) for p in KYOGIIN_PREFIXES):
            session.pop("kyogiin_logged_in", None)
            session.pop("kyogiin_name", None)
    if session.get("admin_rank"):
        if not any(path.startswith(p) for p in ADMIN_PREFIXES):
            session.pop("admin_rank", None)
            session.pop("admin_name", None)

CONFIG_FILE = "config.json"

ALLOWED_GIJIROKU = {"pdf"}
BLOCKED_SHIRYO   = {"docx", "xlsx", "pptx", "doc", "xls", "ppt"}
IMAGE_EXTS       = {"jpg", "jpeg", "png", "gif", "webp"}

def safe_public_id(name):
    name = name.replace("/", "_").replace("\\", "_")
    return name

def strip_month_prefix(name):
    return re.sub(r"^\d{1,2}_", "", name)

def make_public_id(folder_type, month_num, base_name):
    safe_base = safe_public_id(base_name)
    return "jichikai/" + folder_type + "/" + "{:02d}".format(month_num) + "_" + safe_base

def load_config():
    default = {
        "admin2_password_hash": generate_password_hash("admin2-2024"),
        "admin1_users":  {},
        "kyogiin_users": {},
        "file_meta":     {}
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for k, v in default.items():
            data.setdefault(k, v)
        return data
    return default

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def allowed_gijiroku(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_GIJIROKU

JICHIKAI = {
    "name": "立田自治会",
    "tagline": "明るく楽しい元気な立田町",
    "description": "私たち立田自治会は、地域の皆さまが安心して暮らせるまちづくりを目指しています。",
    "email": "jichikai-xxx@example.com",
    "phone": "077-585-0000",
    "address": "滋賀県守山市立田町",
    "meeting_day": "毎月第3土曜日 午後19時30分〜",
    "meeting_place": "集落センター",
    "services": [
        {"icon": "🏘️", "title": "地域の安全・防犯",    "desc": "年末夜間パトロールや防犯灯の管理を行っています。"},
        {"icon": "🌸", "title": "地域イベント",        "desc": "立田フェス・敬老会・清掃活動など、年間を通じてイベントを開催しています。"},
        {"icon": "🚨", "title": "防災・災害対策",      "desc": "避難訓練の実施や備蓄品の管理など、災害に備えた活動を行っています。"},
        {"icon": "♻️", "title": "ごみ・環境美化",      "desc": "ごみ収集ルールの周知と、地域の清掃活動を定期的に実施しています。"},
        {"icon": "👴", "title": "高齢者・福祉サポート", "desc": "一人暮らしの高齢者への見守り活動や、福祉情報の提供を行っています。"},
        {"icon": "📢", "title": "情報共有・広報",      "desc": "回覧板を通じて、地域の最新情報をお届けします。"},
    ],
    "events": [
        {"month": "4月",  "name": "総会"},
        {"month": "5月",  "name": ""},
        {"month": "6月",  "name": "美化運動"},
        {"month": "7月",  "name": ""},
        {"month": "8月",  "name": ""},
        {"month": "9月",  "name": "総会"},
        {"month": "10月", "name": "立田フェス"},
        {"month": "11月", "name": "敬老会・美化運動・防災訓練"},
        {"month": "12月", "name": "夜間パトロール"},
        {"month": "1月",  "name": ""},
        {"month": "2月",  "name": ""},
        {"month": "3月",  "name": ""},
    ]
}

MONTHS = [str(i) + "月" for i in range(1, 13)]

def get_files_by_month(folder_type):
    result = {m: [] for m in MONTHS}
    prefix = "jichikai/" + folder_type + "/"
    try:
        for resource_type in ["raw", "image"]:
            try:
                res = cloudinary.api.resources(
                    type="upload",
                    prefix=prefix,
                    max_results=500,
                    resource_type=resource_type
                )
                for r in res.get("resources", []):
                    public_id = r["public_id"]
                    base_name = public_id.split("/")[-1]
                    fmt = r.get("format", "")
                    fname = base_name + "." + fmt if fmt else base_name
                    prefix_num = base_name.split("_")[0]
                    if prefix_num.isdigit() and 1 <= int(prefix_num) <= 12:
                        result[str(int(prefix_num)) + "月"].append(fname)
            except Exception as e:
                print("Cloudinary " + resource_type + " list error: " + str(e))
    except Exception as e:
        print("Cloudinary get_files_by_month error: " + str(e))
    return result

def get_cloudinary_url(folder_type, fname):
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "dyhtmmqnk")
    if "." in fname:
        base = fname.rsplit(".", 1)[0]
        ext  = fname.rsplit(".", 1)[1].lower()
    else:
        base = fname
        ext  = ""
    public_id = "jichikai/" + folder_type + "/" + base
    if ext in IMAGE_EXTS:
        # 画像はimage/upload
        url, _ = cloudinary_url(public_id, resource_type="image")
    else:
        # PDF等rawファイルはraw/upload
        url = f"https://res.cloudinary.com/{cloud_name}/raw/upload/{public_id}.{ext}"
    return url

def get_display_name(fname):
    parts = fname.split("_", 1)
    return parts[1] if len(parts) > 1 else fname

def get_file_meta(cfg, fname):
    meta = cfg.get("file_meta", {}).get(fname, {})
    return {
        "watermark": meta.get("watermark", True),
        "download":  meta.get("download",  False),
        "print":     meta.get("print",     False),
    }

def admin_rank():
    return session.get("admin_rank", 0)

@app.route("/")
def index():
    return render_template("index.html", company=JICHIKAI)

@app.route("/kyogiin", methods=["GET", "POST"])
def kyogiin():
    if session.get("kyogiin_logged_in"):
        return redirect(url_for("kyogiin_files", month="1月"))
    error = None
    if request.method == "POST":
        cfg      = load_config()
        name     = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        users    = cfg.get("kyogiin_users", {})
        if name in users and users[name].get("active", True):
            if check_password_hash(users[name]["password_hash"], password):
                session["kyogiin_logged_in"] = True
                session["kyogiin_name"]      = name
                return redirect(url_for("kyogiin_files", month="1月"))
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
    if month not in MONTHS:
        month = "1月"
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
        cfg     = load_config()
        cur_pw  = request.form.get("current_password", "").strip()
        new_pw  = request.form.get("new_password", "").strip()
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
    return render_template(
        "kyogiin_change_password.html",
        company=JICHIKAI, user_name=user_name, msg=msg,
    )

@app.route("/kyogiin/view/<file_type>/<path:filename>")
def kyogiin_view_file(file_type, filename):
    if not session.get("kyogiin_logged_in"):
        return redirect(url_for("kyogiin"))
    if file_type not in ("shiryo", "gijiroku"):
        abort(404)
    safe = os.path.basename(filename)
    cfg  = load_config()
    meta = get_file_meta(cfg, safe) if file_type == "shiryo" else {
        "watermark": True, "download": False, "print": False
    }
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""

    # ★ Cloudinaryの直接URLを生成（リダイレクトなし）
    cloudinary_direct_url = get_cloudinary_url(file_type, safe)

    file_url     = url_for("kyogiin_raw_file", file_type=file_type, filename=safe)
    file_url_abs = request.host_url.rstrip("/") + file_url
    return render_template(
        "kyogiin_viewer.html",
        company=JICHIKAI, filename=safe,
        display_name=get_display_name(safe),
        user_name=session.get("kyogiin_name", ""),
        file_url=cloudinary_direct_url,      # ★ 直接URLに変更
        file_url_abs=file_url_abs,
        file_ext=ext,
        watermark=meta["watermark"],
        allow_download=meta["download"],
        allow_print=meta["print"],
        is_pdf=(ext == "pdf"),
        file_type=file_type,
    )

@app.route("/kyogiin/raw/<file_type>/<path:filename>")
def kyogiin_raw_file(file_type, filename):
    if not session.get("kyogiin_logged_in"):
        abort(403)
    safe = os.path.basename(filename)
    if file_type == "shiryo":
        cfg  = load_config()
        meta = get_file_meta(cfg, safe)
        if request.args.get("dl") == "1" and not meta["download"]:
            abort(403)
    url = get_cloudinary_url(file_type, safe)
    return redirect(url)

@app.route("/admin/rank1", methods=["GET", "POST"])
def admin1_login():
    if admin_rank() >= 1:
        return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        cfg      = load_config()
        name     = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        a1 = cfg.get("admin1_users", {})
        if name in a1 and a1[name].get("active", True):
            if check_password_hash(a1[name]["password_hash"], password):
                session["admin_rank"] = 1
                session["admin_name"] = name
                return redirect(url_for("admin_dashboard"))
        error = "名前またはパスワードが違います"
    return render_template("admin1_login.html", company=JICHIKAI, error=error)

@app.route("/admin/change_password", methods=["GET", "POST"])
def admin1_change_password():
    if admin_rank() < 1:
        return redirect(url_for("admin1_login"))
    if admin_rank() == 2:
        return redirect(url_for("admin_dashboard"))
    admin_name = session.get("admin_name", "")
    msg = None
    if request.method == "POST":
        cfg     = load_config()
        cur_pw  = request.form.get("current_password", "").strip()
        new_pw  = request.form.get("new_password", "").strip()
        conf_pw = request.form.get("confirm_password", "").strip()
        a1 = cfg.get("admin1_users", {})
        if admin_name not in a1:
            msg = ("danger", "ユーザーが見つかりません")
        elif not check_password_hash(a1[admin_name]["password_hash"], cur_pw):
            msg = ("danger", "現在のパスワードが違います")
        elif len(new_pw) < 4:
            msg = ("danger", "新しいパスワードは4文字以上で入力してください")
        elif new_pw != conf_pw:
            msg = ("danger", "確認用パスワードが一致しません")
        else:
            cfg["admin1_users"][admin_name]["password_hash"] = generate_password_hash(new_pw)
            save_config(cfg)
            msg = ("success", "パスワードを変更しました")
    return render_template(
        "admin1_change_password.html",
        company=JICHIKAI, admin_name=admin_name, msg=msg,
    )

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if admin_rank() >= 1:
        return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        cfg      = load_config()
        password = request.form.get("password", "").strip()
        if check_password_hash(cfg["admin2_password_hash"], password):
            session["admin_rank"] = 2
            session["admin_name"] = "上位管理者"
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
    if admin_rank() < 1:
        return redirect(url_for("admin_login"))
    cfg = load_config()
    msg = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "upload_shiryo":
            month       = request.form.get("month", "1月")
            file        = request.files.get("file")
            watermark   = request.form.get("watermark") == "1"
            download    = request.form.get("download")  == "1"
            allow_print = request.form.get("print")     == "1"
            if not file or file.filename == "":
                msg = ("danger", "ファイルを選択してください")
            elif file.filename.rsplit(".", 1)[-1].lower() in BLOCKED_SHIRYO:
                msg = ("danger", "Word・Excel・PowerPointはアップロードできません。PDF・画像に変換してください。")
            else:
                month_num = MONTHS.index(month) + 1
                original  = file.filename
                ext       = original.rsplit(".", 1)[-1].lower() if "." in original else ""
                base_name = original.rsplit(".", 1)[0] if "." in original else original
                base_name = strip_month_prefix(base_name)
                save_name = "{:02d}_".format(month_num) + base_name + ("." + ext if ext else "")
                public_id = make_public_id("shiryo", month_num, base_name)
                resource_type = "image" if ext in IMAGE_EXTS else "raw"
                try:
                    cloudinary.uploader.upload(
                        file,
                        public_id="{:02d}_{}".format(month_num, base_name),
                        folder="jichikai/shiryo",
                        resource_type=resource_type,
                        format=ext,
                        use_filename=False,
                        unique_filename=False,
                        overwrite=True
                    )
                    cfg.setdefault("file_meta", {})[save_name] = {
                        "watermark": watermark, "download": download, "print": allow_print,
                    }
                    save_config(cfg)
                    msg = ("success", month + "に資料「" + original + "」をアップロードしました")
                except Exception as e:
                    msg = ("danger", "アップロードエラー: " + str(e))

        elif action == "upload_gijiroku":
            month = request.form.get("month", "1月")
            file  = request.files.get("file")
            if not file or file.filename == "":
                msg = ("danger", "ファイルを選択してください")
            elif not allowed_gijiroku(file.filename):
                msg = ("danger", "議事録はPDFファイルのみアップロードできます")
            else:
                month_num = MONTHS.index(month) + 1
                original  = file.filename
                base_name = original.rsplit(".", 1)[0] if "." in original else original
                base_name = strip_month_prefix(base_name)
                save_name = "{:02d}_".format(month_num) + base_name + ".pdf"
                public_id = make_public_id("gijiroku", month_num, base_name)
                try:
                    cloudinary.uploader.upload(
                        file,
                        public_id="{:02d}_{}".format(month_num, base_name),
                        folder="jichikai/gijiroku",
                        resource_type="raw",
                        format="pdf",
                        use_filename=False,
                        unique_filename=False,
                        overwrite=True
                    )
                    msg = ("success", month + "に議事録「" + original + "」をアップロードしました")
                except Exception as e:
                    msg = ("danger", "アップロードエラー: " + str(e))

        elif action == "delete_shiryo":
            fname         = request.form.get("filename", "")
            ext           = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            base          = fname.rsplit(".", 1)[0] if "." in fname else fname
            resource_type = "image" if ext in IMAGE_EXTS else "raw"
            public_id     = "jichikai/shiryo/" + base
            try:
                cloudinary.uploader.destroy(public_id, resource_type=resource_type)
                cfg.get("file_meta", {}).pop(fname, None)
                save_config(cfg)
                msg = ("success", "資料「" + get_display_name(fname) + "」を削除しました")
            except Exception as e:
                msg = ("danger", "削除エラー: " + str(e))

        elif action == "delete_gijiroku":
            fname     = request.form.get("filename", "")
            base      = fname.rsplit(".", 1)[0] if "." in fname else fname
            public_id = "jichikai/gijiroku/" + base
            try:
                cloudinary.uploader.destroy(public_id, resource_type="raw")
                msg = ("success", "議事録「" + get_display_name(fname) + "」を削除しました")
            except Exception as e:
                msg = ("danger", "削除エラー: " + str(e))

        elif action == "change_admin1_pw":
            name    = session.get("admin_name", "")
            cur_pw  = request.form.get("current_password", "").strip()
            new_pw  = request.form.get("new_password", "").strip()
            conf_pw = request.form.get("confirm_password", "").strip()
            a1 = cfg.get("admin1_users", {})
            if name not in a1:
                msg = ("danger", "ユーザーが見つかりません")
            elif not check_password_hash(a1[name]["password_hash"], cur_pw):
                msg = ("danger", "現在のパスワードが違います")
            elif len(new_pw) < 4:
                msg = ("danger", "新しいパスワードは4文字以上で入力してください")
            elif new_pw != conf_pw:
                msg = ("danger", "確認用パスワードが一致しません")
            else:
                cfg["admin1_users"][name]["password_hash"] = generate_password_hash(new_pw)
                save_config(cfg)
                msg = ("success", "パスワードを変更しました")

        elif admin_rank() < 2:
            msg = ("danger", "この操作はランク2管理者のみ実行できます")

        elif action == "add_kyogiin":
            name    = request.form.get("new_name", "").strip()
            pw      = request.form.get("new_password", "").strip()
            conf_pw = request.form.get("confirm_password", "").strip()
            if not name:
                msg = ("danger", "名前を入力してください")
            elif name in cfg["kyogiin_users"]:
                msg = ("danger", "「" + name + "」はすでに登録されています")
            elif pw != conf_pw:
                msg = ("danger", "確認用パスワードが一致しません")
            else:
                cfg["kyogiin_users"][name] = {
                    "password_hash": generate_password_hash(pw), "active": True
                }
                save_config(cfg)
                msg = ("success", "協議員「" + name + "」を追加しました")

        elif action == "change_kyogiin_pw":
            name    = request.form.get("user_name", "").strip()
            pw      = request.form.get("new_password", "").strip()
            conf_pw = request.form.get("confirm_password", "").strip()
            if name not in cfg["kyogiin_users"]:
                msg = ("danger", "ユーザーが見つかりません")
            elif pw != conf_pw:
                msg = ("danger", "確認用パスワードが一致しません")
            else:
                cfg["kyogiin_users"][name]["password_hash"] = generate_password_hash(pw)
                save_config(cfg)
                msg = ("success", "「" + name + "」のパスワードを変更しました")

        elif action == "toggle_kyogiin":
            name = request.form.get("user_name", "").strip()
            if name in cfg["kyogiin_users"]:
                cur = cfg["kyogiin_users"][name].get("active", True)
                cfg["kyogiin_users"][name]["active"] = not cur
                save_config(cfg)
                msg = ("success", "「" + name + "」を" + ("有効" if not cur else "無効") + "にしました")

        elif action == "delete_kyogiin":
            name = request.form.get("user_name", "").strip()
            if name in cfg["kyogiin_users"]:
                del cfg["kyogiin_users"][name]
                save_config(cfg)
                msg = ("success", "協議員「" + name + "」を削除しました")

        elif action == "add_admin1":
            name    = request.form.get("new_name", "").strip()
            pw      = request.form.get("new_password", "").strip()
            conf_pw = request.form.get("confirm_password", "").strip()
            if not name:
                msg = ("danger", "名前を入力してください")
            elif name in cfg.get("admin1_users", {}):
                msg = ("danger", "「" + name + "」はすでに登録されています")
            elif pw != conf_pw:
                msg = ("danger", "確認用パスワードが一致しません")
            else:
                cfg.setdefault("admin1_users", {})[name] = {
                    "password_hash": generate_password_hash(pw), "active": True
                }
                save_config(cfg)
                msg = ("success", "ランク1管理者「" + name + "」を追加しました")

        elif action == "toggle_admin1":
            name = request.form.get("user_name", "").strip()
            if name in cfg.get("admin1_users", {}):
                cur = cfg["admin1_users"][name].get("active", True)
                cfg["admin1_users"][name]["active"] = not cur
                save_config(cfg)
                msg = ("success", "「" + name + "」を" + ("有効" if not cur else "無効") + "にしました")

        elif action == "delete_admin1":
            name = request.form.get("user_name", "").strip()
            if name in cfg.get("admin1_users", {}):
                del cfg["admin1_users"][name]
                save_config(cfg)
                msg = ("success", "ランク1管理者「" + name + "」を削除しました")

        elif action == "change_admin2_pw":
            cur_pw  = request.form.get("current_password", "").strip()
            new_pw  = request.form.get("new_password", "").strip()
            conf_pw = request.form.get("confirm_password", "").strip()
            if not check_password_hash(cfg["admin2_password_hash"], cur_pw):
                msg = ("danger", "現在のパスワードが違います")
            elif new_pw != conf_pw:
                msg = ("danger", "確認用パスワードが一致しません")
            else:
                cfg["admin2_password_hash"] = generate_password_hash(new_pw)
                save_config(cfg)
                msg = ("success", "ランク2パスワードを変更しました")

        cfg = load_config()

    return render_template(
        "admin_dashboard.html",
        company=JICHIKAI, months=MONTHS,
        shiryo_by_month=get_files_by_month("shiryo"),
        gijiroku_by_month=get_files_by_month("gijiroku"),
        kyogiin_users=cfg.get("kyogiin_users", {}),
        admin1_users=cfg.get("admin1_users", {}),
        file_meta=cfg.get("file_meta", {}),
        admin_rank=admin_rank(),
        admin_name=session.get("admin_name", ""),
        msg=msg,
        get_display_name=get_display_name,
    )

@app.route("/admin/download_config")
def admin_download_config():
    if admin_rank() < 2:
        return redirect(url_for("admin_login"))
    cfg  = load_config()
    data = json.dumps(cfg, ensure_ascii=False, indent=2)
    buf  = io.BytesIO(data.encode("utf-8"))
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="jichikai_config_backup.json",
                     mimetype="application/json")

@app.route("/admin/upload_config", methods=["POST"])
def admin_upload_config():
    if admin_rank() < 2:
        return redirect(url_for("admin_login"))
    file = request.files.get("config_file")
    if not file or file.filename == "":
        return redirect(url_for("admin_dashboard"))
    try:
        data = json.load(file)
        save_config(data)
    except Exception:
        pass
    return redirect(url_for("admin_dashboard"))

@app.route("/ping")
def ping():
    return "pong", 200

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)