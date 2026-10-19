import unittest
import xml.etree.ElementTree as ET
import form_slots

NS = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
def p(value='', pid=1):
    return f'<hp:p id="{pid}" paraPrIDRef="2"><hp:run charPrIDRef="3"><hp:t>{value}</hp:t></hp:run><hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray></hp:p>'
def cell(value, col, row=0):
    return f'<hp:tc borderFillIDRef="4"><hp:subList>{p(value)}</hp:subList><hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="3000" height="1200"/></hp:tc>'
def section(content): return f'<hp:sec {NS}>{content}</hp:sec>'
def apply(xml, values, known=()):
    files={'Contents/section0.xml':xml.encode(), 'Contents/header.xml':b'unchanged'}
    report=form_slots.fill_files(files, values, known)
    return files['Contents/section0.xml'].decode(),report

def structure(xml):
    root=ET.fromstring(xml)
    for parent in root.iter():
        for child in list(parent):
            if child.tag.endswith(('linesegarray','lineBreak')): parent.remove(child)
        parent.attrib.pop('dirty',None)
        parent.text=None; parent.tail=None
    return ET.tostring(root)

class Slots(unittest.TestCase):
    def test_blank_labels_and_preserve_structure(self):
        src=section('<hp:tbl id="10"><hp:tr>'+cell('성 명',0)+cell('',1)+cell('연락처',2)+cell('',3)+'</hp:tr></hp:tbl>')
        out,r=apply(src,{'성 명':'홍길동','연락처':'010-0000-0000'})
        self.assertEqual(r['filled'],['성 명','연락처'])
        self.assertEqual(structure(src),structure(out))
        self.assertIn('홍길동',out); self.assertIn('010-0000-0000',out)
    def test_no_prefix_guess_or_invented_key(self):
        src=section('<hp:tbl><hp:tr>'+cell('성명',0)+cell('',1)+'</hp:tr></hp:tbl>')
        out,r=apply(src,{'성명확인':'잘못된 값'})
        self.assertEqual(out,src); self.assertEqual(r['unmatched'],['성명확인'])
    def test_duplicate_labels_are_ambiguous(self):
        src=section('<hp:tbl>'+''.join('<hp:tr>'+cell('성명',0,row)+cell('',1,row)+'</hp:tr>' for row in range(2))+'</hp:tbl>')
        out,r=apply(src,{'성명':'누구인지 모름'})
        self.assertEqual(out,src); self.assertEqual(r['ambiguous'],['성명'])
    def test_clickhere_keeps_runs_and_controls(self):
        src=section('<hp:p id="1"><hp:run charPrIDRef="2"><hp:ctrl><hp:fieldBegin id="9" type="CLICK_HERE" name="제목"/></hp:ctrl></hp:run><hp:run charPrIDRef="3"><hp:t>입력하세요</hp:t></hp:run><hp:run charPrIDRef="2"><hp:ctrl><hp:fieldEnd beginIDRef="9"/></hp:ctrl></hp:run></hp:p>')
        out,r=apply(src,{'제목':'실제 제목 &amp;'})
        self.assertEqual(structure(src),structure(out)); self.assertEqual(r['filled'],['제목'])
        self.assertNotIn('입력하세요',out)
    def test_empty_clickhere_in_existing_run(self):
        src=section('<hp:p><hp:run charPrIDRef="2"><hp:ctrl><hp:fieldBegin id="9" type="CLICK_HERE" name="제목"/></hp:ctrl><hp:ctrl><hp:fieldEnd beginIDRef="9"/></hp:ctrl></hp:run></hp:p>')
        out,r=apply(src,{'제목':'빈 누름틀 입력'})
        self.assertEqual(r['filled'],['제목']); self.assertIn('<hp:t>빈 누름틀 입력</hp:t>',out)
        self.assertIn('dirty="1"',out); ET.fromstring(out)
    def test_missing_keys_reported(self):
        src=section('<hp:tbl><hp:tr>'+cell('성명',0)+cell('',1)+'</hp:tr></hp:tbl>')
        out,r=apply(src,{})
        self.assertEqual(r['missing'],['성명']); self.assertEqual(out,src)
    def test_other_paragraph_cache_preserved(self):
        src=section(p('성명: 안내')+p('수정하면 안 되는 본문',2))
        out,r=apply(src,{'성명':'홍길동'})
        self.assertEqual(out.count('<hp:linesegarray>'),1)
    def test_second_section_and_numeric_zero(self):
        src=section('<hp:tbl><hp:tr>'+cell('금액',0)+cell('',1)+'</hp:tr></hp:tbl>')
        files={'Contents/section0.xml':section(p('표지')).encode(),'Contents/section1.xml':src.encode()}
        r=form_slots.fill_files(files,{'금액':0})
        self.assertEqual(r['filled'],['금액']); self.assertIn(b'<hp:t>0</hp:t>',files['Contents/section1.xml'])
    def test_box_label_and_multiline(self):
        src=section('<hp:tbl><hp:tr>'+cell('(사유)',0)+'</hp:tr></hp:tbl>')
        out,r=apply(src,{'(사유)':'첫 줄\n둘째 줄'})
        self.assertEqual(r['filled'],['(사유)']); self.assertIn('(사유)<hp:lineBreak/>첫 줄',out)
        self.assertEqual(structure(src),structure(out))
    def test_table_headers(self):
        src=section('<hp:tbl><hp:tr>'+cell('이름',0)+cell('직책',1)+'</hp:tr><hp:tr>'+cell('',0,1)+cell('',1,1)+'</hp:tr></hp:tbl>')
        out,r=apply(src,{'이름':'홍길동','직책':'연구원'})
        self.assertEqual(r['filled'],['이름','직책']); self.assertEqual(structure(src),structure(out))
    def test_empty_does_not_erase(self):
        src=section(p('성명: 입력 안내'))
        out,r=apply(src,{'성명':''})
        self.assertEqual(out,src); self.assertEqual(r['empty'],['성명'])
    def test_inline_preserves_label(self):
        src=section(p('성명: 입력 안내'))
        out,r=apply(src,{'성명':'홍길동'})
        self.assertIn('성명: 홍길동',out); self.assertEqual(r['filled'],['성명'])

class MergedAndSections(unittest.TestCase):
    def test_long_bilingual_field(self):
        src=section('<hp:tbl><hp:tr>'+cell('연수내용(Research activities conducted during post-doc period)',0)+cell('',1)+'</hp:tr></hp:tbl>')
        out,r=apply(src,{'연수내용':'사용자의 긴 연구활동 본문'})
        self.assertEqual(r['filled'],['연수내용']); self.assertIn('사용자의 긴 연구활동 본문',out)
        self.assertEqual(structure(src),structure(out))
    def test_header_uses_cell_addresses_not_list_index(self):
        # A wide merged header is followed by three cells at different actual columns.
        header=cell('SCI',6,0).replace('colSpan="1"','colSpan="4"')
        data=cell('투고: 건',6,1)+cell('게재: 건',8,1)
        src=section('<hp:tbl colCnt="19"><hp:tr>'+header+'</hp:tr><hp:tr>'+data+'</hp:tr></hp:tbl>')
        out,r=apply(src,{'SCI / 투고':2,'SCI / 게재':1})
        self.assertEqual(r['filled'],['SCI / 투고','SCI / 게재']);self.assertIn('투고: 2건',out);self.assertIn('게재: 1건',out)
    def test_date_placeholder(self):
        src=section('<hp:tbl><hp:tr>'+cell('연수기간',0)+cell('20  .  .  . ~ 20  .  .  .',1)+'</hp:tr></hp:tbl>')
        out,r=apply(src,{'연수기간':'2025. 1. 1. ~ 2026. 1. 1.'})
        self.assertEqual(r['filled'],['연수기간'])
    def test_number_and_unit_are_not_labels(self):
        src=section('<hp:tbl><hp:tr>'+cell('00 개',0)+cell('',1)+'</hp:tr></hp:tbl>')
        out,r=apply(src,{'00 개':'임의 내용'})
        self.assertEqual(out,src);self.assertEqual(r['unmatched'],['00 개'])
    def test_explicit_sections_keep_original_numbers(self):
        import forms
        fields=[{'label':'연수내용'}, {'label':'교육활동(연수기간 중 교육, 취업활동 등)'}, {'label':'연수만족도'}, {'label':'선택사유'}]
        request='## 1. 연구내용 (Research Activities)\n- 47,728개 스크리닝\n- 활동 둘\n\n## 2. 교육활동 (Education)\n- ADsP\n## 3. 연구만족도\n매우 만족\n## 4. 선택사유\n- 이유\n\n기재 시 확인 사항: 추측 금지'
        r=forms.explicit_sections(request,fields)
        self.assertEqual(r['연수내용'],'- 47,728개 스크리닝\n- 활동 둘')
        self.assertEqual(r['선택사유'],'- 이유');self.assertEqual(len(r),4)
    def test_explicit_sections_do_not_override_numeric_columns(self):
        import forms
        fields=[{'label':'SCI / 투고','unit':'건'}, {'label':'수상실적','unit':'건'}]
        self.assertEqual(forms.explicit_sections('## 수상실적\n상 이름만 제시',fields),{})
    def test_duplicate_explicit_sections_not_guessed(self):
        import forms
        self.assertEqual(forms.explicit_sections('## 연수내용\n하나\n## 연수내용\n둘',[{'label':'연수내용'}]),{})

if __name__=='__main__': unittest.main()
