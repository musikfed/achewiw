"""Non-invasive regression checks: landing page must not auto-open last project."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
APP=(ROOT/"app/static/app.js").read_text(encoding="utf-8")
HTML=(ROOT/"app/static/index.html").read_text(encoding="utf-8")


def test_public_landing_first_on_every_refresh():
    assert "render();await detectModels()" in APP
    assert "await loadProject(projects.find(p=>p.id===previous)" not in APP
    assert "id=\"goHome\"" in HTML
    assert "function goHome()" in APP


def test_category_filter_does_not_jump_into_manuscript():
    start=APP.index("document.querySelectorAll('[data-kind-filter]').forEach(b=>b.onclick")
    end=APP.index("$('#workType').onchange",start)
    assert "goHome();" in APP[start:end]
    assert "loadProject(" not in APP[start:end]
    assert "stashUnsavedProject()" in APP[APP.index("function goHome()"):APP.index("async function loadProject(id)")]


def test_home_is_independent_of_project_existence():
    assert "$('#editor').hidden=!active;$('#empty').hidden=active;" in APP
    assert "Типография в кармане" in HTML
    assert "Филинохомусы" not in HTML
