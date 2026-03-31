from flask import Flask, render_template

app = Flask(__name__)

JICHIKAI = {
    "name": "立田自治会",
    "tagline": "つながる・助け合う・住みよいまちへ",
    "description": "私たち立田自治会は、地域の皆さまが安心して暮らせるまちづくりを目指しています。",
    "email": "jichikai-xxx@example.com",
    "phone": "077-585-0000",
    "address": "滋賀県守山市立田町",
    "meeting_day": "毎月第3土曜日 午後19時30分〜",
    "meeting_place": "集落センター",
    "services": [
        {"icon": "🏘️", "title": "地域の安全・防犯",    "desc": "定期的な夜間パトロールや防犯灯の管理を行っています。"},
        {"icon": "🌸", "title": "地域イベント",        "desc": "夏祭り・運動会・清掃活動など、年間を通じてイベントを開催しています。"},
        {"icon": "🚨", "title": "防災・災害対策",      "desc": "避難訓練の実施や備蓄品の管理など、災害に備えた活動を行っています。"},
        {"icon": "♻️", "title": "ごみ・環境美化",      "desc": "ごみ収集ルールの周知と、地域の清掃活動を定期的に実施しています。"},
        {"icon": "👴", "title": "高齢者・福祉サポート", "desc": "一人暮らしの高齢者への見守り活動や、福祉情報の提供を行っています。"},
        {"icon": "📢", "title": "情報共有・広報",      "desc": "自治会だよりの発行や回覧板を通じて、地域の最新情報をお届けします。"},
    ],
    "events": [
        {"month": "4月", "name": "総会・役員選出"},
        {"month": "6月", "name": "美化運動"},
        {"month": "7月", "name": "BBQ大会"},
        {"month": "11月", "name": "敬老会・美化運動"},
        {"month": "12月", "name": "夜間パトロール"},
        {"month": "3月", "name": "防災訓練"},
    ]
}

@app.route("/")
def index():
    return render_template("index.html", company=JICHIKAI)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)