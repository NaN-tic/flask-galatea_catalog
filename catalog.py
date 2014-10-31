from flask import Blueprint, render_template, current_app, abort, g, \
    request, url_for, jsonify, session
from galatea.tryton import tryton
from galatea.helpers import cached
from flask.ext.paginate import Pagination
from flask.ext.babel import gettext as _, lazy_gettext
import os

catalog = Blueprint('catalog', __name__, template_folder='templates')

DISPLAY_MSG = lazy_gettext('Displaying <b>{start} - {end}</b> of <b>{total}</b>')

GALATEA_WEBSITE = current_app.config.get('TRYTON_GALATEA_SITE')
SHOP = current_app.config.get('TRYTON_SALE_SHOP')
LIMIT = current_app.config.get('TRYTON_PAGINATION_CATALOG_LIMIT', 20)

Website = tryton.pool.get('galatea.website')
Template = tryton.pool.get('product.template')
Product = tryton.pool.get('product.product')
Menu = tryton.pool.get('esale.catalog.menu')

CATALOG_TEMPLATE_FIELD_NAMES = [
    'name', 'code', 'esale_slug', 'esale_shortdescription', 'esale_price',
    'esale_default_images', 'esale_all_images', 'esale_new', 'esale_hot',
    'esale_sequence',
    ]
CATALOG_PRODUCT_FIELD_NAMES = [
    'code', 'template',
    ]

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

    products = Template.search([
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
    result['price'] = product.esale_price
    result['images'] = product.esale_default_images
    if hasattr(product, 'code'):
        result['code'] = product.code
    codes = []
    for p in product.products:
        if p.code:
            codes.append(p.code)
    result['codes'] = codes
    return jsonify(result)

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

    products = Template.search([
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
            cache_prefix='catalog-product-%s-%s' % (product.id, lang),
            )

@catalog.route("/category/<slug>", endpoint="category_product_en")
@catalog.route("/categoria/<slug>", endpoint="category_product_es")
@catalog.route("/categoria/<slug>", endpoint="category_product_ca")
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
        if menu.default_sort_by == 'price':
            order = [('list_price', 'ASC')]

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain = [
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', [SHOP]),
        ('esale_menus', 'in', [menu.id]),
        ]
    total = Template.search_count(domain)
    offset = (page-1)*limit

    tpls = Template.search_read(domain, offset, limit, order, CATALOG_TEMPLATE_FIELD_NAMES)

    product_domain = [('template', 'in', [tpl['id'] for tpl in tpls])]
    prds = Product.search_read(product_domain, fields_names=CATALOG_PRODUCT_FIELD_NAMES)

    products = []
    for tpl in tpls:
        prods = []
        for prd in prds:
            if prd['template'] == tpl['id']:
                prods.append(prd)
        tpl['products'] = prods
        products.append(tpl)

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
            cache_prefix='catalog-category-product-%s-%s-%s' % (menu.id, lang, page),
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
        cache_prefix='catalog-category-%s' % lang,
        )

@catalog.route("/", endpoint="catalog")
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

    domain = [
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', [SHOP]),
        ]
    total = Template.search_count(domain)
    offset = (page-1)*limit

    order = [('name', 'ASC')]
    tpls = Template.search_read(domain, offset, limit, order, CATALOG_TEMPLATE_FIELD_NAMES)

    product_domain = [('template', 'in', [tpl['id'] for tpl in tpls])]
    prds = Product.search_read(product_domain, fields_names=CATALOG_PRODUCT_FIELD_NAMES)

    products = []
    for tpl in tpls:
        prods = []
        for prd in prds:
            if prd['template'] == tpl['id']:
                prods.append(prd)
        tpl['products'] = prods
        products.append(tpl)

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
            cache_prefix='catalog-category-all-%s-%s' % (lang, page),
            )
