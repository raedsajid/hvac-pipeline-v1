import fitz
from .store import AppError
from .domain import correct_sections
MAX_SIZE=10*1024*1024

def open_pdf(data):
    if not data or not data.startswith(b'%PDF-') or not 8<=len(data)<=MAX_SIZE: raise AppError(400,'Choose a valid PDF up to 10 MB.')
    try: doc=fitz.open(stream=data,filetype='pdf')
    except Exception as exc: raise AppError(400,'This PDF could not be read.') from exc
    if doc.needs_pass or not 1<=len(doc)<=200:
        doc.close(); raise AppError(413,'Use an unencrypted PDF with 1–200 pages.')
    return doc

def parse_pdf(data):
    evidence=[]; scanned=[]
    with open_pdf(data) as doc:
        for page in doc:
            count=0; line_num=0
            for block in page.get_text('dict',sort=True)['blocks']:
                for line in block.get('lines',[]):
                    text=' '.join(s['text'] for s in line['spans']).strip()
                    if not text: continue
                    line_num+=1; count+=len(text)
                    # Text boxes are unrotated; normalize in the visible page's space.
                    box=fitz.Rect(line['bbox'])*page.rotation_matrix; rect=page.rect
                    x0=max(0,min(1,box.x0/rect.width)); y0=max(0,min(1,box.y0/rect.height))
                    x1=max(x0,min(1,box.x1/rect.width)); y1=max(y0,min(1,box.y1/rect.height))
                    evidence.append({'id':f'p{page.number+1}-l{line_num}','page':page.number+1,'text':text[:10000],'box':[x0,y0,x1-x0,y1-y0],'section':'Document','clause':'','source':'pdf','fontSize':max(s['size'] for s in line['spans']),'bold':any(s['flags'] & 16 for s in line['spans'])})
            if count<30: scanned.append(page.number+1)
            if len(evidence)>12000: raise AppError(413,'Split this PDF into smaller documents (over 12,000 text blocks).')
        return correct_sections({'version':'builderp-python-v1','pages':len(doc),'evidence':evidence,'scannedPages':scanned,'sections':[]})

def subset(data,page,box=None):
    with open_pdf(data) as doc:
        if not 1<=page<=len(doc): raise AppError(400,'Invalid page number.')
        with fitz.open() as output:
            output.insert_pdf(doc,from_page=page-1,to_page=page-1)
            if box:
                p=output[0]; visible=p.rect; x,y,w,h=box
                crop=fitz.Rect(x*visible.width,y*visible.height,(x+w)*visible.width,(y+h)*visible.height)*p.derotation_matrix
                # set_cropbox uses media-space coordinates; visible-page regions
                # are relative to the existing CropBox, which may have an offset.
                origin=p.cropbox.tl
                crop=fitz.Rect(crop.x0+origin.x,crop.y0+origin.y,crop.x1+origin.x,crop.y1+origin.y)
                p.set_cropbox(crop)
            return output.tobytes()

def render_page(data,page,boxes=()):
    with open_pdf(data) as doc:
        if not 1<=page<=len(doc): raise AppError(404,'Page not found.')
        p=doc[page-1]; visible=p.rect
        for box in boxes:
            x,y,w,h=box
            rect=fitz.Rect(x*visible.width,y*visible.height,(x+w)*visible.width,(y+h)*visible.height)*p.derotation_matrix
            p.draw_rect(rect,color=(.95,.45,.1),fill=(1,.8,.1),fill_opacity=.2,width=1)
        return p.get_pixmap(matrix=fitz.Matrix(min(1.5,1600/visible.width),min(1.5,1600/visible.width))).tobytes('png')
