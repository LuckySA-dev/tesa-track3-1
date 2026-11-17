# TESA Top Gun Rally 2026 · Track 3-1 · ทีม T18

เครื่องมือรอบเก็บคะแนนที่ 1 (17 พ.ย. ขายแบบ manual): ประเมินมูลค่าล็อตกาแฟ (TV) และวางแผนราคาเสนอขายในตลาดซองปิด

| โฟลเดอร์ | ใช้ทำอะไร |
|---|---|
| `practice-web/` | เว็บบนเครื่อง: ใบประเมิน TV, ตลาดฝึก, ช่วยวางแผนรอบ manual (`http://127.0.0.1:8765/trade`) |
| `tools/manual_helper.py` | ผู้ช่วยรอบ manual แบบไฟล์ข้อความ: ใส่ LOT / RFQ / ผลรอบ แล้วได้ตารางราคาให้พิมพ์ |
| `tools/sim.py` | จำลองตลาดเทียบกลยุทธ์ |
| `agent/` | แกนคำนวณ: สูตร TV (`market_core.py`), เรียนรู้เพดานราคา (`pricing.py`), วางแผนรอบ (`planner.py`) |
| `data/manual/day1.txt` | ตัวอย่าง input ของ manual_helper |

## รัน

```bash
python tools/manual_helper.py data/manual/day1.txt
python -B practice-web/app.py        # ต้องมี engine ของผู้จัดที่ vendor/TesaMarket-build (ไม่รวมใน repo นี้)
```

Python 3.10+ ใช้แค่ standard library · ค่าเพดานผู้ซื้อในโค้ดเป็นค่าสมมติทั่วไป ไม่ใช่ค่าที่วัดได้จริง
