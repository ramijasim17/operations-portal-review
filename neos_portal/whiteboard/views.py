import io
import json

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from PIL import Image

from .access import require_access
from .models import Board, empty_board

MAX_PAGES = 60
BACKGROUNDS = ("blank", "lined", "grid")


@require_access
def board_list(request):
    if request.method == "POST":
        title = request.POST.get("title", "").strip()[:150] or "Untitled board"
        board = Board.objects.create(owner=request.user, title=title)
        return redirect("whiteboard:edit", pk=board.pk)
    return render(request, "whiteboard/board_list.html", {"boards": request.user.whiteboards.all()})


def _own(request, pk):
    return get_object_or_404(Board, pk=pk, owner=request.user)


@require_access
def board_edit(request, pk):
    board = _own(request, pk)
    return render(request, "whiteboard/board_edit.html", {"board": board})


def _clean(data):
    """Keep only what the editor understands, so a bad request can't store junk."""
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        return None
    pages = data["pages"][:MAX_PAGES] or [{"items": []}]
    out = []
    for page in pages:
        items = page.get("items") if isinstance(page, dict) else None
        out.append({"items": [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []})
    bg = data.get("bg") if data.get("bg") in BACKGROUNDS else "lined"
    return {"bg": bg, "pages": out}


@require_access
@require_POST
def board_save(request, pk):
    board = _own(request, pk)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"ok": False, "error": "Bad data"}, status=400)
    data = _clean(payload.get("data"))
    if data is None:
        return JsonResponse({"ok": False, "error": "Bad data"}, status=400)
    board.data = data
    title = str(payload.get("title", "")).strip()[:150]
    if title:
        board.title = title
    board.save()
    return JsonResponse({"ok": True, "saved": board.updated_at.isoformat()})


@require_access
@require_POST
def board_delete(request, pk):
    board = _own(request, pk)
    board.delete()
    messages.success(request, "Board deleted.")
    return redirect("whiteboard:list")


@require_access
@require_POST
def board_export_pdf(request, pk):
    """The browser draws each page (with the ABC logo, title and date) as an
    image; this puts them, one per page, on A4 sheets and returns one PDF."""
    board = _own(request, pk)
    images = []
    for f in request.FILES.getlist("pages")[:MAX_PAGES]:
        try:
            img = Image.open(f)
            img.load()
        except Exception:
            return HttpResponse("One of the pages could not be read.", status=400)
        if img.mode != "RGB":
            flat = Image.new("RGB", img.size, "white")
            flat.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = flat
        images.append(img.resize((1240, 1754)))       # A4 at 150 dpi
    if not images:
        return HttpResponse("Nothing to export.", status=400)
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=150.0)
    response = HttpResponse(buf.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(True, f"{board.title}.pdf")
    return response
