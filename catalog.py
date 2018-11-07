from flask import Blueprint, render_template, current_app, abort, g, \
    request, url_for, jsonify, session, flash
from galatea.tryton import tryton
from galatea.utils import get_tryton_language, thumbnail
from galatea.helpers import cached
from flask_paginate import Pagination
from flask_babel import gettext as _, lazy_gettext, ngettext
from trytond.transaction import Transaction
from trytond.config import config as tryton_config
from whoosh import index
from whoosh.qparser import MultifieldParser
import os

catalog = Blueprint('catalog', __name__, template_folder='templates')

DISPLAY_MSG = lazy_gettext('Displaying <b>{start} - {end}</b> of <b>{total}</b>')

GALATEA_WEBSITE = current_app.config.get('TRYTON_GALATEA_SITE')
SHOP = current_app.config.get('TRYTON_SALE_SHOP')
LIMIT = current_app.config.get('TRYTON_PAGINATION_CATALOG_LIMIT', 20)
WHOOSH_MAX_LIMIT = current_app.config.get('WHOOSH_MAX_LIMIT', 500)
CATALOG_ORDER_PRICE = current_app.config.get('TRYTON_CATALOG_ORDER_PRICE', 'esale_global_price')
MENU_CATEGORY = current_app.config.get('TRYTON_CATALOG_MENU_CATEGORY', False)
CATALOG_SCHEMA_PARSE_FIELDS = current_app.config.get(
    'TRYTON_CATALOG_SCHEMA_PARSE_FIELDS', ['title', 'content'])
CATALOG_SEARCH_ADD_WILDCARD = current_app.config.get(
    'TRYTON_CATALOG_SEARCH_ADD_WILDCARD', False)

Website = tryton.pool.get('galatea.website')
Template = tryton.pool.get('product.template')
Product = tryton.pool.get('product.product')
Category = tryton.pool.get('product.category')
Menu = tryton.pool.get('esale.catalog.menu')

CATALOG_TEMPLATE_FILTERS = []

def catalog_ordered(default='name'):
    '''Catalog Product Order'''
    if request.args.get('order'):
        option_order = request.args.get('order')
        if session.get('catalog_order') == option_order:
            order = option_order
        else:
            # check param is a field searchable
            if option_order in [k for k, v in Template().fields_get([]).iteritems() if v['searchable']]:
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
    website = Website(GALATEA_WEBSITE)

    with Transaction().set_context(without_special_price=True):
        products = Template.search([
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_slug', '=', slug),
            ('esale_active', '=', True),
            ('shops', 'in', [SHOP]),
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
                ('template.shops', 'in', [SHOP]),
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
    WHOOSH_CATALOG_DIR = current_app.config.get('WHOOSH_CATALOG_DIR')
    if not WHOOSH_CATALOG_DIR:
        abort(404)

    db_name = current_app.config.get('TRYTON_DATABASE')
    locale = get_tryton_language(lang)

    schema_dir = os.path.join(tryton_config.get('database', 'path'),
        db_name, 'whoosh', WHOOSH_CATALOG_DIR, locale.lower()[:2])

    if not os.path.exists(schema_dir):
        abort(404)

    website = Website(GALATEA_WEBSITE)

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
            limit = LIMIT
    else:
        limit = session.get('catalog_limit', LIMIT)

    # view
    if request.args.get('view'):
        view = 'grid'
        if request.args.get('view') == 'list':
            view = 'list'
        session['catalog_view'] = view

    # Search
    ix = index.open_dir(schema_dir)
    query = q.replace('+', ' AND ').replace('-', ' NOT ')
    if CATALOG_SEARCH_ADD_WILDCARD:
        phrases = []
        for phrase in query.split('"')[1::2]:
            phrases.append('"' + phrase + '"')
        words = []
        for word in ' '.join(query.split('"')[0::2]).split():
            if word and word not in ['AND', 'NOT', 'OR']:
                word = '("' + word + '" OR *' + word + '*)'
            words.append(word)
        query = " ".join(phrases + words)
    query = MultifieldParser(CATALOG_SCHEMA_PARSE_FIELDS, ix.schema).parse(query)

    with ix.searcher() as s:
        all_results = s.search_page(query, 1, pagelen=WHOOSH_MAX_LIMIT)
        total = all_results.scored_length()
        results = s.search_page(query, page, pagelen=limit) # by pagination
        res = [result.get('id') for result in results]

    domain = [('id', 'in', res)]

    with Transaction().set_context(without_special_price=True):
        products = Template.search(domain, order=catalog_ordered())

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

    if request.args.get('format') == 'json':
        return jsonify([{
                    'name': product.name,
                    'url': url_for('.product_'+g.language, lang=g.language,
                        slug=product.esale_slug),
                    'image': thumbnail(
                        product.esale_default_images['small']['digest'],
                        product.esale_default_images['small']['name'],
                        '100x100')
                    }
                for product in products])
    else:
        return render_template('catalog-search.html',
            website=website,
            products=products,
            pagination=pagination,
            breadcrumbs=breadcrumbs,
            q=q,
            )

@catalog.route("/product/<slug>", endpoint="product_en")
@catalog.route("/producto/<slug>", endpoint="product_es")
@catalog.route("/producte/<slug>", endpoint="product_ca")
@tryton.transaction()
def product(lang, slug):
    '''Product Details

    slug param is a product slug or a product code
    '''
    template = request.args.get('template', None)

    # template
    if template:
        blueprintdir = os.path.dirname(__file__)
        basedir = '/'.join(blueprintdir.split('/')[:-1])
        if not os.path.isfile('%s/templates/%s.html' % (basedir, template)):
            template = None
    if not template:
        template = 'catalog-product'

    website = Website(GALATEA_WEBSITE)

    with Transaction().set_context(without_special_price=True):
        products = Template.search([
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_slug', '=', slug),
            ('esale_active', '=', True),
            ('shops', 'in', [SHOP]),
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
                ('template.shops', 'in', [SHOP]),
                ], limit=1)
        if products:
            product = products[0].template

    if not product:
        abort(404)

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
            )

@catalog.route("/key/<key>", endpoint="key")
@tryton.transaction()
def key(lang, key):
    '''Products by Key'''
    website = Website(GALATEA_WEBSITE)

    # limit
    if request.args.get('limit'):
        try:
            limit = int(request.args.get('limit'))
            session['catalog_limit'] = limit
        except:
            limit = LIMIT
    else:
        limit = session.get('catalog_limit', LIMIT)

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
        for k, v in request.form.iteritems():
            if k in CATALOG_TEMPLATE_FILTERS:
                domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['catalog_filter'] = domain_filter

    domain = [
        ('salable', '=', True),
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('shops', 'in', [SHOP]),
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
            )

@catalog.route("/category/<slug>", methods=["GET", "POST"], endpoint="category_product_en")
@catalog.route("/categoria/<slug>", methods=["GET", "POST"], endpoint="category_product_es")
@catalog.route("/categoria/<slug>", methods=["GET", "POST"], endpoint="category_product_ca")
@tryton.transaction()
def category_products(lang, slug):
    '''Category Products'''
    website = Website(GALATEA_WEBSITE)

    if MENU_CATEGORY:
        menus = Category.search([
            ('slug', '=', slug),
            ('esale_active', '=', True),
            ('website', '=', website),
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
            limit = LIMIT
    else:
        limit = session.get('catalog_limit', LIMIT)

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
        order = CATALOG_ORDER_PRICE
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
        for k, v in request.form.iteritems():
            if k in CATALOG_TEMPLATE_FILTERS:
                domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['catalog_filter'] = domain_filter

    domain = [
        ('salable', '=', True),
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('shops', 'in', [SHOP]),
        ] + domain_filter
    if MENU_CATEGORY:
        domain.append(('categories', 'in', [menu.id]))
    else:
        domain.append(('esale_menus', 'in', [menu.id]))

    total = Template.search_count(domain)
    offset = (page-1)*limit

    with Transaction().set_context(without_special_price=True):
        products = Template.search(domain, offset, limit, order)

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

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
            )

@catalog.route("/category/", endpoint="category_en")
@catalog.route("/categoria/", endpoint="category_es")
@catalog.route("/categoria/", endpoint="category_ca")
@tryton.transaction()
def category(lang):
    '''All category'''
    website = Website(GALATEA_WEBSITE)

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
    website = Website(GALATEA_WEBSITE)

    # limit
    if request.args.get('limit'):
        try:
            limit = int(request.args.get('limit'))
            session['catalog_limit'] = limit
        except:
            limit = LIMIT
    else:
        limit = session.get('catalog_limit', LIMIT)

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
        for k, v in request.form.iteritems():
            if k in CATALOG_TEMPLATE_FILTERS:
                domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['catalog_filter'] = domain_filter

    domain = [
        ('salable', '=', True),
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('shops', 'in', [SHOP]),
        ] + domain_filter

    # Search
    if request.args.get('q'):
        qstr = request.args.get('q')
        session.q = qstr
        phrases = qstr.split('"')[1::2]
        for phrase in phrases:
            domain.append(
                ('rec_name', 'ilike', '%{}%'.format(phrase.encode('utf-8'))))
        words = ' '.join(qstr.split('"')[0::2]).split()
        for word in words:
            domain.append(
                ('rec_name', 'ilike', '%{}%'.format(word.encode('utf-8'))))
        flash(_('Search results for "{qstr}"').format(qstr=qstr))
    else:
        session.q = None

    total = Template.search_count(domain)
    offset = (page-1)*limit

    with Transaction().set_context(without_special_price=True):
        products = Template.search(domain, offset, limit, order=catalog_ordered())

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

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
            )
