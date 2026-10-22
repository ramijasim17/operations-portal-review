import os

from django.contrib import messages
from django.db.models import ProtectedError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import (
    can_delete_required, can_download_required, can_edit_required, user_can,
)

from .forms import ControlledDocumentForm, DocumentTypeForm, PublishingOrganizationForm
from .models import ControlledDocument, DocumentType, PublishingOrganization

LIST_URL = "document_control:list"

SORT_OPTIONS = {
    "date": "-date_of_issue",
    "date_asc": "date_of_issue",
    "name": "name",
    "-name": "-name",
    "number": "document_number",
    "type": "document_type__name",
}


def document_list(request):
    """Everyone can view the list. Creating a document (the POST from the
    New document popup) needs Edit access -- checked inline because this
    one view serves both the read-only list and the create form."""
    documents = ControlledDocument.objects.select_related("document_type", "published_by").all()
    q = request.GET.get("q")
    doc_type = request.GET.get("type")
    sort = request.GET.get("sort", "date")
    if q:
        documents = documents.filter(name__icontains=q) | documents.filter(document_number__icontains=q)
    if doc_type:
        documents = documents.filter(document_type_id=doc_type)
    documents = documents.order_by(SORT_OPTIONS.get(sort, "-date_of_issue"))

    if request.method == "POST":
        if not user_can(request, "can_edit"):
            messages.error(request, "You don't have permission to create documents.")
            return redirect(LIST_URL)
        form = ControlledDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save()
            messages.success(request, f"'{doc.name}' uploaded.")
            return redirect(LIST_URL)
    else:
        form = ControlledDocumentForm()

    return render(request, "document_control/document_list.html", {
        "documents": documents, "q": q or "", "doc_type": doc_type or "", "sort": sort,
        "document_types": DocumentType.objects.all(),
        "form": form,
    })


@can_download_required(redirect_to=LIST_URL)
def document_download(request, pk):
    """The only route to a document's file. Streams it through Django
    after checking permission, so the raw storage URL is never handed to
    someone without Download access."""
    doc = get_object_or_404(ControlledDocument, pk=pk)
    if not doc.file:
        raise Http404("This document has no file attached.")
    try:
        return FileResponse(doc.file.open("rb"), filename=os.path.basename(doc.file.name))
    except (FileNotFoundError, OSError):
        messages.error(request, "This document's file is missing from storage \u2014 re-upload it from Edit.")
        return redirect(LIST_URL)


@can_edit_required(redirect_to=LIST_URL)
def document_edit(request, pk):
    doc = get_object_or_404(ControlledDocument, pk=pk)
    if request.method == "POST":
        form = ControlledDocumentForm(request.POST, request.FILES, instance=doc)
        if form.is_valid():
            form.save()
            messages.success(request, "Document updated.")
            return redirect(LIST_URL)
    else:
        form = ControlledDocumentForm(instance=doc)
    return render(request, "document_control/document_form.html", {"form": form, "document": doc})


@can_delete_required(redirect_to=LIST_URL)
def document_delete(request, pk):
    doc = get_object_or_404(ControlledDocument, pk=pk)
    if request.method == "POST":
        doc.delete()
        messages.success(request, "Document deleted.")
        return redirect(LIST_URL)
    return render(request, "document_control/document_confirm_delete.html", {"document": doc})


@can_edit_required(redirect_to=LIST_URL)
def document_type_manage(request):
    types = DocumentType.objects.all()
    if request.method == "POST":
        form = DocumentTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Document type added.")
            return redirect("document_control:type_manage")
    else:
        form = DocumentTypeForm()
    return render(request, "document_control/type_manage.html", {"types": types, "form": form})


@can_edit_required(redirect_to=LIST_URL)
def document_type_edit(request, pk):
    doc_type = get_object_or_404(DocumentType, pk=pk)
    if request.method == "POST":
        form = DocumentTypeForm(request.POST, instance=doc_type)
        if form.is_valid():
            form.save()
            messages.success(request, "Document type updated.")
            return redirect("document_control:type_manage")
    else:
        form = DocumentTypeForm(instance=doc_type)
    return render(request, "document_control/type_form.html", {"form": form, "doc_type": doc_type})


@can_delete_required(redirect_to=LIST_URL)
def document_type_delete(request, pk):
    doc_type = get_object_or_404(DocumentType, pk=pk)
    if request.method == "POST":
        try:
            doc_type.delete()
            messages.success(request, "Document type deleted.")
        except ProtectedError:
            messages.error(request, f"Can't delete '{doc_type.name}' \u2014 documents are still using it.")
        return redirect("document_control:type_manage")
    return render(request, "document_control/type_confirm_delete.html", {"doc_type": doc_type})


@can_edit_required(redirect_to=LIST_URL)
def publisher_manage(request):
    orgs = PublishingOrganization.objects.all()
    if request.method == "POST":
        form = PublishingOrganizationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Publisher added.")
            return redirect("document_control:publisher_manage")
    else:
        form = PublishingOrganizationForm()
    return render(request, "document_control/publisher_manage.html", {"orgs": orgs, "form": form})


@can_edit_required(redirect_to=LIST_URL)
def publisher_edit(request, pk):
    org = get_object_or_404(PublishingOrganization, pk=pk)
    if request.method == "POST":
        form = PublishingOrganizationForm(request.POST, instance=org)
        if form.is_valid():
            form.save()
            messages.success(request, "Publisher updated.")
            return redirect("document_control:publisher_manage")
    else:
        form = PublishingOrganizationForm(instance=org)
    return render(request, "document_control/publisher_form.html", {"form": form, "org": org})


@can_delete_required(redirect_to=LIST_URL)
def publisher_delete(request, pk):
    org = get_object_or_404(PublishingOrganization, pk=pk)
    if request.method == "POST":
        try:
            org.delete()
            messages.success(request, "Publisher deleted.")
        except ProtectedError:
            messages.error(request, f"Can't delete '{org.name}' \u2014 documents are still using it.")
        return redirect("document_control:publisher_manage")
    return render(request, "document_control/publisher_confirm_delete.html", {"org": org})
