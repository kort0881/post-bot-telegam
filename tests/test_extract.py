from bot.extract import extract_from_html

ARTICLE_HTML = """
<html><head><title>t</title><style>.x{color:red}</style></head>
<body>
  <nav><a href="/a">Главная</a><a href="/b">Новости</a></nav>
  <div class="sidebar advert">Купить VPN со скидкой, промокод внутри</div>
  <article>
    <h1>РКН расширил блокировку</h1>
    <p>Провайдеры начали фильтрацию трафика на уровне DPI: часть VLESS-подключений
       перестала устанавливаться, пользователи сообщают о разрывах соединения.</p>
    <p>Роскомсвобода опубликовала инструкцию, как проверить, блокируется ли конкретный
       сервер или протокол целиком, и какие параметры стоит изменить после обновления.</p>
    <p>Отдельно отмечается, что полный обход не гарантирован: часть операторов применяет
       дополнительные механизмы обнаружения трафика, которые пока изучены не полностью.</p>
    <script>console.log('ad')</script>
    <div class="share-buttons">Поделиться в соцсетях</div>
  </article>
  <footer>© 2026</footer>
</body></html>
"""


def test_extract_keeps_content_drops_nav_and_ads():
    text = extract_from_html(ARTICLE_HTML, min_chars=200)
    assert text
    assert "DPI" in text
    assert "Роскомсвобода" in text
    assert "Главная" not in text
    assert "промокод" not in text.lower()
    assert "console.log" not in text
    assert "Поделиться" not in text


def test_extract_returns_none_for_thin_page():
    assert extract_from_html("<html><body><p>коротко</p></body></html>", min_chars=400) is None


def test_extract_returns_none_for_garbage():
    assert extract_from_html("", min_chars=100) is None
    assert extract_from_html("x" * 300, min_chars=100) is None
