# -*- coding: utf-8 -*-
"""专项验证：判定框兜底逻辑（hitboxes 字段为空时按命名规律推导探测）。"""
import re
import requests

API = "https://www.dustloop.com/wiki/api.php"
UA = {"User-Agent": "AstrBot-DustloopGBVSR-Plugin-Test/1.0"}


def cargo(params):
    r = requests.get(API, params={**params, "format": "json"}, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json()


def image_urls(filenames):
    titles = "|".join(f"File:{f}" for f in filenames)
    r = requests.get(API, params={"action": "query", "titles": titles,
                                  "prop": "imageinfo", "iiprop": "url", "format": "json"},
                     headers=UA, timeout=20)
    return [p["imageinfo"][0]["url"] for p in r.json()["query"]["pages"].values()
            if p.get("imageinfo")]


def hitbox_urls(row):
    files = [f for f in row.get("hitboxes", "").split("\\") if f.strip()]
    if files:
        return image_urls(files[:6]), "字段直取"
    bases = []
    for f in row.get("images", "").split("\\"):
        f = f.strip()
        if f:
            bases.append(re.sub(r"\.(png|jpe?g|gif)$", "", f, flags=re.I))
    suffixes = ("_Hitbox", "_Hitbox1", "_Hitbox2", "_Hitbox3",
                "_Hitbox_1", "_Hitbox_2", "_Hitbox_3",
                "_Hitbox-1", "_Hitbox-2", "_Hitbox-3", "_Hitbox-4", "_Hitbox-5")
    candidates = []
    for b in bases[:3]:
        candidates.extend(b + s + ".png" for s in suffixes)
    if not candidates:
        return [], "无图可推导"
    return image_urls(candidates), "推导探测"


def match_moves(rows, query):
    q = re.sub(r"(?i)[lmhu]", lambda m: m.group(0).upper(), query.strip().replace(" ", ""))
    ql = q.lower()
    variants = [q]
    m = re.fullmatch(r"5([LMHU])", q)
    if m:
        variants = [q, f"c.{m.group(1)}", f"f.{m.group(1)}"]
    exact = [r for r in rows if r["input"].replace(" ", "") in variants]
    if exact:
        return exact
    nob = [r for r in rows if re.sub(r"[\[\]]", "", r["input"].replace(" ", "")) == q]
    if nob:
        return nob
    return [r for r in rows if ql in r["input"].replace(" ", "").lower()
            or ql in r.get("name", "").lower()]


if __name__ == "__main__":
    data = cargo({
        "action": "cargoquery", "tables": "MoveData_GBVSR",
        "fields": "input,name,images,hitboxes,hitboxCaption",
        "where": 'chara="Gran"', "limit": 500,
    })
    rows = [r["title"] for r in data["cargoquery"]]

    # 场景1：用户输入 5U -> 应命中 5[U] Power Raise（方括号兼容）
    hit = match_moves(rows, "5U")
    print(f"[{'OK' if hit else 'FAIL'}] '5U' 匹配: {[r['input'] for r in hit]}")
    assert any(r["input"] == "5[U]" for r in hit), "5U 未命中 5[U]"

    # 场景2：Power Raise 的 hitboxes 字段为空，但应能通过推导找到判定框图
    pr = next(r for r in rows if r["input"] == "5[U]")
    print(f"      5[U] hitboxes 字段={pr['hitboxes']!r} images={pr['images']!r}")
    urls, how = hitbox_urls(pr)
    print(f"[{'OK' if urls else 'FAIL'}] Power Raise 判定框（{how}）: {len(urls)} 张")
    for u in urls:
        print("      ", u)
        assert requests.head(u, headers=UA, timeout=20).status_code == 200
    assert urls, "兜底推导未找到图片"

    # 场景3：Cancel（5[U]~X）连普通图都没有，应正确返回空
    cancel = next(r for r in rows if r["input"] == "5[U]~X")
    urls2, how2 = hitbox_urls(cancel)
    print(f"[OK] Cancel 判定框: {len(urls2)} 张（{how2}，预期为 0）")

    # 场景4：正常招式仍走字段直取
    cl = next(r for r in rows if r["input"] == "c.L")
    urls3, how3 = hitbox_urls(cl)
    print(f"[OK] c.L 判定框（{how3}）: {urls3}")
    assert urls3 and how3 == "字段直取"

    print("\n兜底逻辑全部验证通过 ✅")
