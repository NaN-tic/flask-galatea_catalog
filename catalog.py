from flask import Blueprint, render_template, current_app, abort, g, request, url_for
from galatea.tryton import tryton
from galatea.utils import get_tryton_locale
from flask.ext.paginate import Pagination
from flask.ext.babel import gettext as _

catalog = Blueprint('catalog', __name__, template_folder='templates')

DISPLAY_MSG = _('Displaying <b>{start} - {end}</b> {record_name} in total <b>{total}</b>')

galatea_website = current_app.config.get('TRYTON_GALATEA_SITE')
shops = current_app.config.get('TRYTON_SALE_SHOPS')
limit = current_app.config.get('TRYTON_PAGINATION_CATALOG_LIMIT')
locations = current_app.config.get('TRYTON_LOCATIONS')

Website = tryton.pool.get('galatea.website')
Template = tryton.pool.get('product.template')
Menu = tryton.pool.get('esale.catalog.menu')

@tryton.default_context
def default_context():
    language = get_tryton_locale(g.language)
    return {'language': language, 'locations': locations}

@catalog.route("/product/<slug>", endpoint="product_en")
@catalog.route("/producto/<slug>", endpoint="product_es")
@catalog.route("/producte/<slug>", endpoint="product_ca")
@tryton.transaction()
def product(lang, slug):
    '''Product Details'''
    websites = Website.search([
        ('id', '=', galatea_website),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    products = Template.search([
        ('esale_slug', '=', slug),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', shops),
        ], limit=1)

    if not products:
        abort(404)
    product, = products

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        }, {
        'slug': url_for('.category_'+g.language, lang=g.language),
        'name': _('Product'),
        }, {
        'slug': url_for('.product_'+g.language, lang=g.language, slug=product.esale_slug),
        'name': product.name,
        }]

    return render_template('catalog-product.html',
            website=website,
            product=product,
            breadcrumbs=breadcrumbs,
            )

@catalog.route("/category/<slug>", endpoint="category_product_en")
@catalog.route("/categoria/<slug>", endpoint="category_product_es")
@catalog.route("/categoria/<slug>", endpoint="category_product_ca")
@tryton.transaction()
def category_products(lang, slug):
    '''Category Products'''
    websites = Website.search([
        ('id', '=', galatea_website),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    menus = Menu.search([
        ('slug', '=', slug),
        ('active', '=', True),
        ], limit=1)
    if not menus:
        abort(404)
    menu, = menus

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
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', shops),
        ('esale_menus', 'in', [menu.id]),
        ]
    total = Template.search_count(domain)
    offset = (page-1)*limit

    fields_names = ['name', 'esale_slug', 'esale_shortdescription',
            'list_price', 'esale_default_images', 'esale_all_images', 'esale_new', 'esale_hot']
    products = Template.search_read(domain, offset, limit, order, fields_names)

    pagination = Pagination(page=page, total=total, per_page=limit, display_msg=DISPLAY_MSG, bs_version='3')

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.catalog', lang=g.language),
        'name': _('Catalog'),
        }, {
        'slug': url_for('.category_'+g.language, lang=g.language),
        'name': _('Category'),
        }, {
        'slug': url_for('.category_product_'+g.language, lang=g.language, slug=menu.slug),
        'name': menu.name,
        }]

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
        ('id', '=', galatea_website),
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

@catalog.route("/", endpoint="catalog")
@tryton.transaction()
def catalog_all(lang):
    '''All catalog products'''
    websites = Website.search([
        ('id', '=', galatea_website),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain = [
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', shops),
        ]
    total = Template.search_count(domain)
    offset = (page-1)*limit

    order = [('name', 'ASC')]
    fields_names = ['name', 'esale_slug', 'esale_shortdescription',
            'list_price', 'esale_default_images', 'esale_all_images', 'esale_new', 'esale_hot']
    products = Template.search_read(domain, offset, limit, order, fields_names)

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
