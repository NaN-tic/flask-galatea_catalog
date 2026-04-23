from flask import Blueprint, render_template, current_app, abort, g, \
    request, url_for, jsonify, session, flash
from app_extensions import tryton
from galatea.utils import thumbnail
from galatea.helpers import cached
from flask_paginate import Pagination
from flask_babel import gettext as _, lazy_gettext
from trytond.transaction import Transaction
from trytond.config import config as tryton_config
from whoosh import index
from whoosh.qparser import MultifieldParser
import os

catalog = Blueprint('catalog', __name__, template_folder='templates')

DISPLAY_MSG = lazy_gettext('Displaying <b>{start} - {end}</b> of <b>{total}</b>')

CATALOG_TEMPLATE_FILTERS = []


def get_shop_id():
    return current_app.config.get('TRYTON_SALE_SHOP')


def get_galatea_website():
    return current_app.config.get('TRYTON_GALATEA_SITE')


def get_limit():
    return current_app.config.get('TRYTON_PAGINATION_CATALOG_LIMIT', 20)


def get_whoosh_max_limit():
    return current_app.config.get('WHOOSH_MAX_LIMIT', 500)


def get_catalog_order_price():
    return current_app.config.get('TRYTON_CATALOG_ORDER_PRICE', 'esale_global_price')


def get_menu_category():
    return current_app.config.get('TRYTON_CATALOG_MENU_CATEGORY', False)


def get_catalog_schema_parse_fields():
    return current_app.config.get('TRYTON_CATALOG_SCHEMA_PARSE_FIELDS', ['title', 'content'])


def get_catalog_search_add_wildcard():
    return current_app.config.get('TRYTON_CATALOG_SEARCH_ADD_WILDCARD', False)

def catalog_ordered(default='name'):
    '''Catalog Product Order'''
    Template = tryton.pool.get('product.template')
    if request.args.get('order'):
        option_order = request.args.get('order')
        if session.get('catalog_order') == option_order:
            order = option_order
        else:
            # check param is a field searchable
            if option_order in [k for k, v in Template().fields_get([]).items() if v['searchable']]:
                order = option_order
                session['catalog_order'] = order
            elif session.get('catalog_order'):
                order = session['catalog_order']
            else:
                order = 'name'
    elif session.get('catalog_order'):
        order = session['catalog_order']
    else:
        order = default

    order_direction = request.args.get('order_direction')
    if order_direction not in ['ASC', 'DESC']:
        if order == 'create_date' or order == 'write_date':
            order_direction = 'DESC'
        else:
            order_direction = 'ASC'
    session['catalog_order_direction'] = order_direction

    if order != 'name':
        if order == 'create_date' or order == 'write_date':
            order = [(order, order_direction), ('name', 'ASC')]
        else:
            order = [(order, order_direction), ('name', order_direction)]
    else:
        order = [('name', order_direction)]
    return order

@catalog.route("/json/<slug>", endpoint="product_json")
@tryton.transaction()
@cached(3500, 'catalog-product-detail-json')
def product_json(lang, slug):
    '''Product JSON Details

    slug param is a product slug or a product code
    '''
    Template = tryton.pool.get('product.template')
    Product = tryton.pool.get('product.product')
    with Transaction().set_context(without_special_price=True):
        products = Template.search([
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_slug', '=', slug),
            ('esale_active', '=', True),
            ('shops', 'in', [get_shop_id()]),
            ], limit=1)

    product = None
    if products:
        product, = products

    if not product:
        # search product by code
        with Transaction().set_context(without_special_price=True):
            products = Product.search([
                ('template.esale_available', '=', True),
                ('code', '=', slug),
                ('template.esale_active', '=', True),
                ('template.shops', 'in', [get_shop_id()]),
                ], limit=1)
            if products:
                product = products[0].template

    if not product:
        abort(404)

    result = {}
    result['name'] = product.name
    result['url'] = '%s%s' % (current_app.config['BASE_URL'], url_for(
        'catalog.product_'+g.language, lang=g.language, slug=product.esale_slug))
    result['shortdescription'] = product.esale_shortdescription
    price = product.esale_price
    if price:
        result['price'] = float(price)
    result['images'] = product.esale_default_images
    if hasattr(product, 'code'):
        result['code'] = product.code
    codes = []
    for p in product.products:
        if p.code:
            codes.append(p.code)
    result['codes'] = codes
    return jsonify(result)

@catalog.route("/search/", methods=["GET"], endpoint="search")
@tryton.transaction()
def search(lang):
    '''Search'''
    Website = tryton.pool.get('galatea.website')
    Template = tryton.pool.get('product.template')
    Product = tryton.pool.get('product.product')
    Category = tryton.pool.get('product.category')
    Shop = tryton.pool.get('sale.shop')

    WHOOSH_CATALOG_DIR = current_app.config.get('WHOOSH_CATALOG_DIR')
    if not WHOOSH_CATALOG_DIR:
        abort(404)

    db_name = current_app.config.get('TRYTON_DATABASE')
    schema_dir = os.path.join(tryton_config.get('database', 'path'),
        db_name, 'whoosh', WHOOSH_CATALOG_DIR, lang)

    if not os.path.exists(schema_dir):
        abort(404)

    website = Website(get_galatea_website())

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        }, {
        'slug': url_for('.search', lang=g.language),
        'name': _('Search'),
        }]

    q = request.args.get('q')
    if not q:
        return render_template('catalog-search.html',
                website=website,
                products=[],
                breadcrumbs=breadcrumbs,
                pagination=None,
                q=None,
                )
    session['q'] = q

    # Get products from schema results
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    # limit
    if request.args.get('limit'):
        try:
            limit = int(request.args.get('limit'))
            session['catalog_limit'] = limit
        except:
            limit = get_limit()
    else:
        limit = session.get('catalog_limit', get_limit())

    # view
    if request.args.get('view'):
        view = 'grid'
        if request.args.get('view') == 'list':
            view = 'list'
        session['catalog_view'] = view

    # Search
    ix = index.open_dir(schema_dir)
    query = q.replace('+', ' AND ').replace('-', ' NOT ')
    if get_catalog_search_add_wildcard():
        phrases = []
        for phrase in query.split('"')[1::2]:
            phrases.append('"' + phrase + '"')
        words = []
        for word in ' '.join(query.split('"')[0::2]).split():
            if word and word not in ['AND', 'NOT', 'OR']:
                word = '("' + word + '" OR *' + word + '*)'
            words.append(word)
        query = " ".join(phrases + words)
    query = MultifieldParser(get_catalog_schema_parse_fields(), ix.schema).parse(query)

    with ix.searcher() as s:
        all_results = s.search_page(query, 1, pagelen=get_whoosh_max_limit())
        total = all_results.scored_length()
        results = s.search_page(query, page, pagelen=limit) # by pagination
        res = [result.get('id') for result in results]

    domain = [('id', 'in', res)]

    with Transaction().set_context(without_special_price=True):
        products = Template.search(domain, order=catalog_ordered())

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

    if request.args.get('format') == 'json':
        results = []
        for product in products:
            result = {
                'name': product.name,
                'url': url_for('.product_'+g.language, lang=g.language,
                    slug=product.esale_slug)
            }
            if product.esale_default_images['small']:
                result['image'] = thumbnail(
                    product.esale_default_images['small']['digest'],
                    product.esale_default_images['small']['name'],
                    '100x100',
                    )
            results.append(result)
        return jsonify(results)
    else:
        return render_template('catalog-search.html',
            website=website,
            products=products,
            pagination=pagination,
            breadcrumbs=breadcrumbs,
            shop=Shop(get_shop_id()),
            q=q,
            )

@catalog.route("/product/<slug>", endpoint="product_en")
@catalog.route("/producto/<slug>", endpoint="product_es")
@catalog.route("/producte/<slug>", endpoint="product_ca")
@catalog.route("/produit/<slug>", endpoint="product_fr")
@catalog.route("/product/<slug>", endpoint="product_de")
@catalog.route("/product/<slug>", endpoint="product_it")
@catalog.route("/product/<slug>", endpoint="product_pl")
@catalog.route("/product/<slug>", endpoint="product_pt")
@tryton.transaction()
def product(lang, slug):
    '''Product Details

    slug param is a product slug or a product code
    '''
    Website = tryton.pool.get('galatea.website')
    User = tryton.pool.get('galatea.user')
    Template = tryton.pool.get('product.template')
    Product = tryton.pool.get('product.product')
    Shop = tryton.pool.get('sale.shop')
    template = request.args.get('template', None)

    # template
    if template:
        blueprintdir = os.path.dirname(__file__)
        basedir = '/'.join(blueprintdir.split('/')[:-1])
        if not os.path.isfile('%s/templates/%s.html' % (basedir, template)):
            template = None
    if not template:
        template = 'catalog-product'

    website = Website(get_galatea_website())

    with Transaction().set_context(without_special_price=True):
        products = Template.search([
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_slug', '=', slug),
            ('esale_active', '=', True),
            ('shops', 'in', [get_shop_id()]),
            ], limit=1)

    product = None
    if products:
        product, = products

    if not product:
        # search product by code
        with Transaction().set_context(without_special_price=True):
            products = Product.search([
                ('template.esale_available', '=', True),
                ('code', '=', slug),
                ('template.esale_active', '=', True),
                ('template.shops', 'in', [get_shop_id()]),
                ], limit=1)
        if products:
            product = products[0].template

    if not product:
        abort(404)

    session['next'] = url_for('.product_'+g.language, lang=g.language, slug=product.esale_slug)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        }, {
        'slug': url_for('.category_'+g.language, lang=g.language),
        'name': _('Categories'),
        }, {
        'slug': url_for('.product_'+g.language, lang=g.language, slug=product.esale_slug),
        'name': product.name,
        }]

    return render_template('%s.html' % template,
            website=website,
            product=product,
            breadcrumbs=breadcrumbs,
            shop=Shop(get_shop_id())
            )

@catalog.route("/key/<key>", endpoint="key")
@tryton.transaction()
def key(lang, key):
    '''Products by Key'''
    Website = tryton.pool.get('galatea.website')
    Template = tryton.pool.get('product.template')
    Product = tryton.pool.get('product.product')
    Shop = tryton.pool.get('sale.shop')

    website = Website(get_galatea_website())

    # limit
    if request.args.get('limit'):
        try:
            limit = int(request.args.get('limit'))
            session['catalog_limit'] = limit
        except:
            limit = get_limit()
    else:
        limit = session.get('catalog_limit', get_limit())

    # view
    if request.args.get('view'):
        view = 'grid'
        if request.args.get('view') == 'list':
            view = 'list'
        session['catalog_view'] = view

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain_filter = session.get('catalog_filter', [])
    if request.form:
        domain_filter = []
        domain_filter_keys = set()
        for k, v in request.form.items():
            if k in CATALOG_TEMPLATE_FILTERS:
                domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['catalog_filter'] = domain_filter

    domain = [
        ('salable', '=', True),
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('shops', 'in', [get_shop_id()]),
        ('esale_metakeyword', 'ilike', '%'+key+'%'),
        ] + domain_filter

    # Search
    if request.args.get('q'):
        qstr = request.args.get('q')
        q = '%' + qstr + '%'
        domain.append(
            ('rec_name', 'ilike', q),
            )
        session.q = qstr
        flash(_('Search results for "{qstr}"').format(qstr=qstr))
    else:
        session.q = None

    total = Template.search_count(domain)
    offset = (page-1)*limit

    with Transaction().set_context(without_special_price=True):
        products = Template.search(domain, offset, limit, order=catalog_ordered())

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

    session['next'] = url_for('.key', lang=g.language, key=key)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        }, {
        'slug': url_for('.key', lang=g.language, key=key),
        'name': key,
        }, ]

    return render_template('catalog-key.html',
            website=website,
            pagination=pagination,
            products=products,
            breadcrumbs=breadcrumbs,
            key=key,
            shop=Shop(get_shop_id())
            )

@catalog.route("/category/<slug>", methods=["GET", "POST"], endpoint="category_product_en")
@catalog.route("/categoria/<slug>", methods=["GET", "POST"], endpoint="category_product_es")
@catalog.route("/categoria/<slug>", methods=["GET", "POST"], endpoint="category_product_ca")
@catalog.route("/categorie/<slug>", methods=["GET", "POST"], endpoint="category_product_fr")
@catalog.route("/category/<slug>", methods=["GET", "POST"], endpoint="category_product_de")
@catalog.route("/category/<slug>", methods=["GET", "POST"], endpoint="category_product_it")
@catalog.route("/category/<slug>", methods=["GET", "POST"], endpoint="category_product_pl")
@catalog.route("/category/<slug>", methods=["GET", "POST"], endpoint="category_product_pt")
@tryton.transaction()
def category_products(lang, slug):
    '''Category Products'''
    Website = tryton.pool.get('galatea.website')
    User = tryton.pool.get('galatea.user')
    Template = tryton.pool.get('product.template')
    Product = tryton.pool.get('product.product')
    Category = tryton.pool.get('product.category')
    Menu = tryton.pool.get('esale.catalog.menu')
    Shop = tryton.pool.get('sale.shop')

    website = Website(get_galatea_website())
    user_id = session.get('user')

    if get_menu_category():
        menus = Category.search([
            ('slug', '=', slug),
            ('esale_active', '=', True),
            ('websites', 'in', [website]),
            ], limit=1)
    else:
        menus = Menu.search([
            ('slug', '=', slug),
            ('active', '=', True),
            ('website', '=', website),
            ], limit=1)
    if not menus:
        abort(404)
    menu, = menus

    # limit
    if request.args.get('limit'):
        try:
            limit = int(request.args.get('limit'))
            session['catalog_limit'] = limit
        except:
            limit = get_limit()
    else:
        limit = session.get('catalog_limit', get_limit())

    # view
    if request.args.get('view'):
        view = 'grid'
        if request.args.get('view') == 'list':
            view = 'list'
        session['catalog_view'] = view

    # order
    if menu.default_sort_by == 'position':
        order = 'esale_sequence'
    elif menu.default_sort_by == 'price':
        order = get_catalog_order_price()
    elif menu.default_sort_by == 'date':
        order = 'create_date'
    else:
        order = 'name'
    order = catalog_ordered(order)

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain_filter = session.get('catalog_filter', [])
    if request.form:
        domain_filter = []
        domain_filter_keys = set()
        for k, v in request.form.items():
            if k in CATALOG_TEMPLATE_FILTERS:
                domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['catalog_filter'] = domain_filter

    domain = [
        ('salable', '=', True),
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('shops', 'in', [get_shop_id()]),
        ] + domain_filter
    if get_menu_category():
        domain.append(('categories', 'in', [menu.id]))
    else:
        domain.append(('esale_menus', 'in', [menu.id]))

    if user_id:
        user = User(user_id)
        if hasattr(user, 'catalog_product_domain'):
            catalog_product_domain = User.catalog_product_domain(user, session, website)
            if catalog_product_domain:
                domain += catalog_product_domain

    total = Template.search_count(domain)
    offset = (page-1)*limit

    with Transaction().set_context(without_special_price=True):
        products = Template.search(domain, offset, limit, order)

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

    session['next'] = url_for('.catalog', lang=g.language)

    #breadcumbs
    breadcrumbs = []
    breadcrumbs.append({
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        })

    def breadcumb_category(menu, categories):
        if menu.parent:
            categories.append(menu.parent)
            breadcumb_category(menu.parent, categories)
        return categories
    categories = breadcumb_category(menu, [])
    if categories:
        categories.pop()
        categories.reverse()

    for category in categories:
        breadcrumbs.append({
            'slug': url_for('.category_product_'+g.language,
                lang=g.language, slug=category.slug),
            'name': category.name,
            })

    breadcrumbs.append({
        'slug': url_for('.category_product_'+g.language,
            lang=g.language, slug=menu.slug),
        'name': menu.name,
        })

    return render_template('catalog-category-product.html',
            website=website,
            menu=menu,
            pagination=pagination,
            products=products,
            breadcrumbs=breadcrumbs,
            shop=Shop(get_shop_id())
            )

@catalog.route("/category/", endpoint="category_en")
@catalog.route("/categoria/", endpoint="category_es")
@catalog.route("/categoria/", endpoint="category_ca")
@catalog.route("/categorie/", endpoint="category_fr")
@catalog.route("/category/", endpoint="category_de")
@catalog.route("/category/", endpoint="category_it")
@catalog.route("/category/", endpoint="category_pl")
@catalog.route("/category/", endpoint="category_pt")
@tryton.transaction()
def category(lang):
    '''All category'''
    Website = tryton.pool.get('galatea.website')
    Category = tryton.pool.get('product.category')

    website = Website(get_galatea_website())

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        }, {
        'slug': url_for('.category_'+g.language, lang=g.language),
        'name': _('Category'),
        }]

    return render_template('catalog-category.html',
        website=website,
        breadcrumbs=breadcrumbs,
        )

@catalog.route("/", methods=["GET", "POST"], endpoint="catalog")
@tryton.transaction()
def catalog_all(lang):
    '''All catalog products'''
    Website = tryton.pool.get('galatea.website')
    User = tryton.pool.get('galatea.user')
    Template = tryton.pool.get('product.template')
    Category = tryton.pool.get('product.category')
    Shop = tryton.pool.get('sale.shop')

    website = Website(get_galatea_website())
    user_id = session.get('user')

    # limit
    if request.args.get('limit'):
        try:
            limit = int(request.args.get('limit'))
            session['catalog_limit'] = limit
        except:
            limit = get_limit()
    else:
        limit = session.get('catalog_limit', get_limit())

    # view
    if request.args.get('view'):
        view = 'grid'
        if request.args.get('view') == 'list':
            view = 'list'
        session['catalog_view'] = view

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain_filter = session.get('catalog_filter', [])
    if request.form:
        domain_filter = []
        domain_filter_keys = set()
        for k, v in request.form.items():
            if k in CATALOG_TEMPLATE_FILTERS:
                domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['catalog_filter'] = domain_filter

    domain = [
        ('salable', '=', True),
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('shops', 'in', [get_shop_id()]),
        ] + domain_filter

    if user_id:
        user = User(user_id)
        if hasattr(user, 'catalog_product_domain'):
            catalog_product_domain = User.catalog_product_domain(user, session, website)
            if catalog_product_domain:
                domain += catalog_product_domain

    # Search
    if request.args.get('q'):
        qstr = request.args.get('q')
        session.q = qstr
        phrases = qstr.split('"')[1::2]
        for phrase in phrases:
            domain.append(
                ('rec_name', 'ilike', '%{}%'.format(phrase)))
        words = ' '.join(qstr.split('"')[0::2]).split()
        for word in words:
            domain.append(
                ('rec_name', 'ilike', '%{}%'.format(word)))
        flash(_('Search results for "{qstr}"').format(qstr=qstr))
    else:
        session.q = None

    total = Template.search_count(domain)
    offset = (page-1)*limit

    with Transaction().set_context(without_special_price=True):
        products = Template.search(domain, offset, limit, order=catalog_ordered())

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

    session['next'] = url_for('.catalog', lang=g.language)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        }]

    return render_template('catalog.html',
            website=website,
            pagination=pagination,
            products=products,
            breadcrumbs=breadcrumbs,
            shop=Shop(get_shop_id())
            )
