from flask import Blueprint, render_template, current_app, abort, g, \
    request, url_for, jsonify, session, flash
from galatea.tryton import tryton
from galatea.utils import get_tryton_language
from galatea.helpers import cached
from flask.ext.paginate import Pagination
from flask.ext.babel import gettext as _, lazy_gettext
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

Website = tryton.pool.get('galatea.website')
Template = tryton.pool.get('product.template')
Product = tryton.pool.get('product.product')
Menu = tryton.pool.get('esale.catalog.menu')

CATALOG_TEMPLATE_FILTERS = []
CATALOG_SCHEMA_PARSE_FIELDS = ['title', 'content']

@catalog.route("/json/<slug>", endpoint="product_json")
@tryton.transaction()
@cached(3500, 'catalog-product-detail-json')
def product_json(lang, slug):
    '''Product JSON Details

    slug param is a product slug or a product code
    '''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    with Transaction().set_context(without_special_price=True):
        products = Template.search([
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_slug', '=', slug),
            ('esale_active', '=', True),
            ('esale_saleshops', 'in', [SHOP]),
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
                ('template.esale_saleshops', 'in', [SHOP]),
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
        db_name, 'whoosh', WHOOSH_CATALOG_DIR, locale.lower())

    if not os.path.exists(schema_dir):
        abort(404)

    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

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
    query = MultifieldParser(CATALOG_SCHEMA_PARSE_FIELDS, ix.schema).parse(query)

    with ix.searcher() as s:
        all_results = s.search_page(query, 1, pagelen=WHOOSH_MAX_LIMIT)
        total = all_results.scored_length()
        results = s.search_page(query, page, pagelen=limit) # by pagination
        res = [result.get('id') for result in results]

    domain = [('id', 'in', res)]
    order = [('name', 'ASC')]

    with Transaction().set_context(without_special_price=True):
        products = Template.search(domain, order=order)

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

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

    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    with Transaction().set_context(without_special_price=True):
        products = Template.search([
            ('salable', '=', True),
            ('esale_available', '=', True),
            ('esale_slug', '=', slug),
            ('esale_active', '=', True),
            ('esale_saleshops', 'in', [SHOP]),
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
                ('template.esale_saleshops', 'in', [SHOP]),
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
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

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
        ('esale_saleshops', 'in', [SHOP]),
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
        flash(_("Your search is \"%s\"." % qstr))
    else:
        session.q = None

    total = Template.search_count(domain)
    offset = (page-1)*limit

    with Transaction().set_context(without_special_price=True):
        order = [('name', 'ASC')]
        products = Template.search(domain, offset, limit, order)

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        }, {
        'slug': url_for('.key', lang=g.language, key=key),
        'name': key,
        }, ]

    return render_template('catalog.html',
            website=website,
            pagination=pagination,
            products=products,
            breadcrumbs=breadcrumbs,
            )

@catalog.route("/category/<slug>", methods=["GET", "POST"], endpoint="category_product_en")
@catalog.route("/categoria/<slug>", methods=["GET", "POST"], endpoint="category_product_es")
@catalog.route("/categoria/<slug>", methods=["GET", "POST"], endpoint="category_product_ca")
@tryton.transaction()
def category_products(lang, slug):
    '''Category Products'''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

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

    order = []
    if menu.default_sort_by:
        if menu.default_sort_by == 'position':
            order = [('esale_sequence', 'ASC')]
        if menu.default_sort_by == 'name':
            order = [('name', 'ASC')]
        # TODO
        # if menu.default_sort_by == 'price':
            # order = [('list_price', 'ASC')]

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
        ('esale_saleshops', 'in', [SHOP]),
        ('esale_menus', 'in', [menu.id]),
        ] + domain_filter
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
    categories.pop()
    if categories:
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
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

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
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

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
        ('esale_saleshops', 'in', [SHOP]),
        ] + domain_filter

    # Search
    if request.args.get('q'):
        qstr = request.args.get('q')
        q = '%' + qstr + '%'
        domain.append(
            ('rec_name', 'ilike', q),
            )
        session.q = qstr
        flash(_("Your search is \"%s\"." % qstr))
    else:
        session.q = None

    total = Template.search_count(domain)
    offset = (page-1)*limit

    with Transaction().set_context(without_special_price=True):
        order = [('name', 'ASC')]
        products = Template.search(domain, offset, limit, order)

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
