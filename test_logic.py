# -*- coding: utf-8 -*-
"""独立验证脚本（不依赖 AstrBot）：测试数据抓取与匹配逻辑。"""
import re
import sys
import requests

API = "https://www.dustloop.com/wiki/api.php"
UA = {"User-Agent": "AstrBot-DustloopGBVSR-Plugin-Test/1.0"}


def cargo(params):
    r = requests.get(API, params={**params, "format": "json"}, headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("info"))
    return data


def strip_markup(text):
    if not text:
        return ""
    t = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
    t = re.sub(r"\[\[([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    return t.replace("'''", "").replace("''", "").strip()


def test_roster():
    data = cargo({"action": "cargoquery", "tables": "MoveData_GBVSR",
                  "fields": "chara", "group_by": "chara", "limit": 500})
    chars = sorted({row["title"]["chara"] for row in data["cargoquery"]})
    print(f"[OK] 角色数: {len(chars)}，样例: {chars[:5]} ... {chars[-3:]}")
    assert "Gran" in chars and "Djeeta (EX)" in chars
    return chars


def test_moves(chara="Gran"):
    data = cargo({
        "action": "cargoquery", "tables": "MoveData_GBVSR",
        "fields": "chara,name,input,damage,guard,startup,active,recovery,onBlock,onHit,onCH,level,invuln,images,hitboxes,hitboxCaption,notes",
        "where": f'chara="{chara}"', "limit": 500,
    })
    rows = [r["title"] for r in data["cargoquery"]]
    print(f"[OK] {chara} 招式数: {len(rows)}")
    return rows


def test_match(rows, q):
    ql = q.lower().replace(" ", "")
    variants = [q]
    m = re.fullmatch(r"5([LMHUlmhu])", q)
    if m:
        btn = m.group(1).upper()
        variants = [q, f"c.{btn}", f"f.{btn}"]
    exact = [r for r in rows if r["input"].replace(" ", "") in variants]
    fuzzy = [r for r in rows if ql in r["input"].replace(" ", "").lower() or ql in r.get("name", "").lower()]
    hit = exact or fuzzy
    print(f"[{'OK' if hit else 'FAIL'}] 查询 '{q}' -> 匹配 {len(hit)} 条: "
          + ", ".join(f"{r['input']}({r.get('name') or '拳脚'})" for r in hit[:5]))
    return hit


def test_images(row, field="hitboxes"):
    files = [f for f in row.get(field, "").split("\\") if f.strip()]
    if not files:
        print(f"[--] {row['input']} 无{field}图片")
        return
    titles = "|".join(f"File:{f}" for f in files)
    r = requests.get(API, params={"action": "query", "titles": titles,
                                  "prop": "imageinfo", "iiprop": "url", "format": "json"},
                     headers=UA, timeout=20)
    urls = [p["imageinfo"][0]["url"] for p in r.json()["query"]["pages"].values() if p.get("imageinfo")]
    # 验证图片可下载
    for u in urls:
        resp = requests.head(u, headers=UA, timeout=20)
        assert resp.status_code == 200, f"图片 404: {u}"
    print(f"[OK] {row['input']} 图片 {len(urls)} 张均可访问: {urls[0]} ...")


if __name__ == "__main__":
    roster = test_roster()
    rows = test_moves("Gran")
    hit_5l = test_match(rows, "5L")
    hit_5l_lower = test_match(rows, "5l")
    hit_special = test_match(rows, "Catastrophe")
    hit_cmd = test_match(rows, "236236U")
    test_match(rows, "不存在的招式")
    # 帧数字段完整性
    r5l = hit_5l[0]
    print("      5L 数据:", {k: strip_markup(v) if k in ("onBlock", "onHit", "onCH") else v
                           for k, v in r5l.items() if k not in ("chara", "images", "notes")})
    test_images(r5l)
    test_images(hit_special[0])
    # EX 角色测试
    rows_ex = test_moves("Djeeta (EX)")
    print(f"[OK] Djeeta (EX) 招式数: {len(rows_ex)}")
    print("\n全部测试通过 ✅")
