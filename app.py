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

# Cloudinary設定
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

def strip_month_prefix(name):
    return re.sub(r"^\d{1,2}_", "", name)

def load_config():
    default = {
        "admin2_password_hash": generate_password_hash("admin2-2024"),
        "admin1_users":  {},
        "kyogiin_users": {},
        "file_meta":     {}
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            try:
                data = json.load(f)
                for k, v in default.items():
                    data.setdefault(k, v)
                return data
            except:
                return default
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
        {"icon": "🏘️", "title": "地域の安全・防犯", "desc": "年末夜間パトロールや防犯灯の管理を行っています。"},
        {"icon": "🌸", "title": "地域イベント", "desc": "立田フェス・敬老会・清掃活動など、年間を通じてイベントを開催しています。"},
        {"icon": "🚨", "title": "防災・災害対策", "desc": "避難訓練の実施や備蓄品の管理など、災害に備えた活動を行っています。"},
        {"icon": "♻️", "title": "ごみ・環境美化", "desc": "ごみ収集ルールの周知と、地域の清掃活動を定期的に実施しています。"},
        {"icon": "👴", "title": "高齢者・福祉サポート", "desc": "一人暮らしの高齢者への見守り活動や、福祉情報の提供を行っています。"},
        {"icon": "📢", "title": "情報共有・広報", "desc": "回覧板を通じて、地域の最新情報をお届けします。"},
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

MONTHS = [str(i) + "月" for i in range(1, 13)]

def get_files_by_month(folder_type):
    result = {m: [] for m in MONTHS}
    prefix = f"jichikai/{folder_type}/"
    try:
        for r_type in ["image", "raw"]:
            res = cloudinary.api.resources(
                type="upload", prefix=prefix, max_results=500, resource_type=r_type
            )
            for r in res.get("resources", []):
                public_id = r["public_id"]
                base_name = public_id.split("/")[-1]
                fmt = r.get("format", "")
                
                # 画像・PDFの拡張子復元
                if "." in base_name:
                    fname = base_name
                elif fmt:
                    fname = f"{base_name}.{fmt.lower()}"
                else:
                    fname = base_name

                prefix_num = base_name.split("_")[0]
                if prefix_num.isdigit() and 1 <= int(prefix_num) <= 12:
                    month_key = str(int(prefix_num)) + "月"
                    if fname not in result[month_key]:
                        result[month_key].append(fname)
    except Exception as e:
        print(f"Cloudinary list error: {e}")
    return result

def get_cloudinary_url(folder_type, fname):
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "dyhtmmqnk")
    if "." in fname:
        base, ext = fname.rsplit(".", 1)
        ext = ext.lower()
    else:
        base, ext = fname, ""
    public_id = f"jichikai/{folder_type}/{base}"
    if ext == "pdf" or ext in IMAGE_EXTS:
        return f"https://res.cloudinary.com/{cloud_name}/image/upload/{public_id}.{ext}"
    else:
        return f"https://res.cloudinary.com/{cloud_name}/raw/upload/{public_id}.{ext}"

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
        cfg = load_config()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        users = cfg.get("kyogiin_users", {})
        if name in users and users[name].get("active", True):
            if check_password_hash(users[name]["password_hash"], password):
                session["kyogiin_logged_in"] = True
                session["kyogiin_name"] = name
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
    if month not in MONTHS: month = "1月"
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
        cur_pw, new_pw, conf_pw = request.form.get("current_password", ""), request.form.get("new_password", ""), request.form.get("confirm_password", "")
        if not check_password_hash(cfg["kyogiin_users"][user_name]["password_hash"], cur_pw):
            msg = ("danger", "現在のパスワードが違います")
        elif len(new_pw) < 4:
            msg = ("danger", "4文字以上で入力してください")
        elif new_pw != conf_pw:
            msg = ("danger", "確認用が一致しません")
        else:
            cfg["kyogiin_users"][user_name]["password_hash"] = generate_password_hash(new_pw)
            save_config(cfg)
            msg = ("success", "パスワードを変更しました")
    return render_template("kyogiin_change_password.html", company=JICHIKAI, user_name=user_name, msg=msg)

@app.route("/kyogiin/view/<file_type>/<path:filename>")
def kyogiin_view_file(file_type, filename):
    if not session.get("kyogiin_logged_in"): return redirect(url_for("kyogiin"))
    if file_type not in ("shiryo", "gijiroku"): abort(404)
    safe = os.path.basename(filename)
    cfg = load_config()
    meta = get_file_meta(cfg, safe) if file_type == "shiryo" else {"watermark": True, "download": False, "print": False}
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    file_url = url_for("kyogiin_raw_file", file_type=file_type, filename=safe)
    return render_template(
        "kyogiin_viewer.html", company=JICHIKAI, filename=safe, display_name=get_display_name(safe),
        user_name=session.get("kyogiin_name", ""), file_url=file_url,
        file_url_abs=request.host_url.rstrip("/") + file_url, file_ext=ext,
        watermark=meta["watermark"], allow_download=meta["download"], allow_print=meta["print"],
        is_pdf=(ext == "pdf"), file_type=file_type
    )

@app.route("/kyogiin/raw/<file_type>/<path:filename>")
def kyogiin_raw_file(file_type, filename):
    if not session.get("kyogiin_logged_in"): abort(403)
    safe = os.path.basename(filename)
    if file_type == "shiryo":
        cfg = load_config()
        meta = get_file_meta(cfg, safe)
        if request.args.get("dl") == "1" and not meta["download"]: abort(403)
    return redirect(get_cloudinary_url(file_type, safe))

# HTML側の admin1_login を受け付けるために名前を修正
@app.route("/admin/rank1", methods=["GET", "POST"])
def admin1_login():
    if admin_rank() >= 1: return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        cfg = load_config()
        name, password = request.form.get("name", "").strip(), request.form.get("password", "").strip()
        a1 = cfg.get("admin1_users", {})
        if name in a1 and a1[name].get("active", True):
            if check_password_hash(a1[name]["password_hash"], password):
                session["admin_rank"], session["admin_name"] = 1, name
                return redirect(url_for("admin_dashboard"))
        error = "名前またはパスワードが違います"
    return render_template("admin1_login.html", company=JICHIKAI, error=error)

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if admin_rank() >= 1: return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        cfg = load_config()
        password = request.form.get("password", "").strip()
        if check_password_hash(cfg["admin2_password_hash"], password):
            session["admin_rank"], session["admin_name"] = 2, "上位管理者"
            return redirect(url_for("admin_dashboard"))
        error = "パスワードが違います"
    return render_template("admin_login.html", company=JICHIKAI, error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/admin/dashboard", methods=["GET", "POST"])
def admin_dashboard():
    if admin_rank() < 1: return redirect(url_for("admin_login"))
    cfg, msg = load_config(), None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "upload_shiryo":
            month, file = request.form.get("month", "1月"), request.files.get("file")
            wm, dl, pr = request.form.get("watermark") == "1", request.form.get("download") == "1", request.form.get("print") == "1"
            if not file or file.filename == "": msg = ("danger", "ファイル未選択")
            elif file.filename.rsplit(".", 1)[-1].lower() in BLOCKED_SHIRYO: msg = ("danger", "PDF化してください")
            else:
                m_num = MONTHS.index(month) + 1
                orig = file.filename
                ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else ""
                base = strip_month_prefix(orig.rsplit(".", 1)[0])
                p_id = "{:02d}_{}".format(m_num, base)
                r_type = "image" if (ext in IMAGE_EXTS or ext == "pdf") else "raw"
                try:
                    cloudinary.uploader.upload(file, public_id=p_id, folder="jichikai/shiryo", resource_type=r_type, use_filename=True, unique_filename=False, overwrite=True)
                    cfg.setdefault("file_meta", {})[f"{p_id}.{ext}"] = {"watermark": wm, "download": dl, "print": pr}
                    save_config(cfg)
                    msg = ("success", f"{month}に資料「{orig}」をアップロードしました")
                except Exception as e: msg = ("danger", f"失敗: {e}")
        elif action == "upload_gijiroku":
            month, file = request.form.get("month", "1月"), request.files.get("file")
            if not file or not allowed_gijiroku(file.filename): msg = ("danger", "PDFのみ")
            else:
                m_num, orig = MONTHS.index(month) + 1, file.filename
                base = strip_month_prefix(orig.rsplit(".", 1)[0])
                try:
                    cloudinary.uploader.upload(file, public_id="{:02d}_{}".format(m_num, base), folder="jichikai/gijiroku", resource_type="image", use_filename=True, unique_filename=False, overwrite=True)
                    msg = ("success", f"{month}に議事録「{orig}」をアップロードしました")
                except Exception as e: msg = ("danger", f"失敗: {e}")
        elif action == "delete_shiryo":
            fname = request.form.get("filename", "")
            base, ext = fname.rsplit(".", 1) if "." in fname else (fname, "")
            r_type = "image" if (ext.lower() in IMAGE_EXTS or ext.lower() == "pdf") else "raw"
            try:
                cloudinary.uploader.destroy(f"jichikai/shiryo/{base}", resource_type=r_type)
                cfg.get("file_meta", {}).pop(fname, None)
                save_config(cfg)
                msg = ("success", "削除しました")
            except Exception as e: msg = ("danger", f"失敗: {e}")
        elif admin_rank() == 2:
            if action == "add_kyogiin":
                n, p, cp = request.form.get("new_name", ""), request.form.get("new_password", ""), request.form.get("confirm_password", "")
                if n and p == cp:
                    cfg["kyogiin_users"][n] = {"password_hash": generate_password_hash(p), "active": True}
                    save_config(cfg); msg = ("success", "追加成功")
            elif action == "toggle_kyogiin":
                n = request.form.get("user_name", "")
                if n in cfg["kyogiin_users"]:
                    cfg["kyogiin_users"][n]["active"] = not cfg["kyogiin_users"][n].get("active", True)
                    save_config(cfg); msg = ("success", "切替成功")
        cfg = load_config()
    return render_template("admin_dashboard.html", company=JICHIKAI, months=MONTHS, shiryo_by_month=get_files_by_month("shiryo"), gijiroku_by_month=get_files_by_month("gijiroku"), kyogiin_users=cfg.get("kyogiin_users", {}), admin1_users=cfg.get("admin1_users", {}), file_meta=cfg.get("file_meta", {}), admin_rank=admin_rank(), admin_name=session.get("admin_name", ""), msg=msg, get_display_name=get_display_name)

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

@app.route("/ping")
def ping(): return "pong", 200

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)