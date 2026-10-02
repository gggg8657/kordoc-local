// kordoc patch 가 위치를 못 잡아 건너뛴 편집을 문단 텍스트 대조로 반영한다 (app.py 윤문에서 호출).
//   node textpatch.mjs 입력.hwpx edits.json 출력.hwpx   edits: [{before, after}]
// 본문·표 셀(중첩 표 포함)·글상자 문단 중 텍스트에 before 가 든 문단을 찾아 after 로 바꾼다.
// kordoc 의 buildParagraphSplices 를 써서 run·글자모양은 그대로 두고 hp:t 내용만 바꾼다. 머리말·꼬리말·각주는 건드리지 않는다.
import { readFileSync, writeFileSync } from "node:fs"
import JSZip from "jszip"
import { scanSectionXml, buildParagraphSplices, applySplices } from "kordoc"

const [src, editsPath, dst] = process.argv.slice(2)
const edits = JSON.parse(readFileSync(editsPath, "utf8"))
const zip = await JSZip.loadAsync(readFileSync(src))
const names = Object.keys(zip.files).filter(n => /^Contents\/section\d+\.xml$/.test(n))
  .sort((a, b) => +a.match(/\d+/)[0] - +b.match(/\d+/)[0])
const norm = s => s.replace(/\s+/g, " ").trim()

const secs = []
for (const [i, name] of names.entries()) {
  const xml = await zip.file(name).async("string"), scan = scanSectionXml(xml, i), paras = [...scan.bodyParagraphs]
  const walk = t => t.rows.flat().forEach(c => { paras.push(...c.paragraphs); c.tables.forEach(walk) })
  scan.tables.forEach(walk)
  secs.push({ name, xml, paras, next: new Map() })  // next: para → 바뀐 텍스트
}
const report = edits.map(({ before, after }) => {
  const b = norm(before); let hits = 0
  for (const s of secs) for (const p of s.paras) {
    const cur = s.next.get(p) ?? norm(p.text)
    if (cur.includes(b)) { s.next.set(p, cur.split(b).join(norm(after))); hits++ }
  }
  return { before, hits }
})
for (const s of secs) {
  if (!s.next.size) continue
  const sp = []
  for (const [p, text] of s.next) { const e = buildParagraphSplices(p, text, s.xml); if (e) sp.push(...e) }
  zip.file(s.name, applySplices(s.xml, sp))
}
const mime = zip.file("mimetype")
if (mime) { const m = await mime.async("string"); zip.remove("mimetype"); const z2 = new JSZip(); z2.file("mimetype", m, { compression: "STORE" })
  for (const [n, f] of Object.entries(zip.files)) if (!f.dir) z2.file(n, await f.async("uint8array")); writeFileSync(dst, await z2.generateAsync({ type: "nodebuffer", compression: "DEFLATE" })) }
else writeFileSync(dst, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }))
console.log(JSON.stringify(report))
