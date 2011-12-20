# -*- coding: utf-8 -*-
"""
Decorators use by the TurboGears controllers.

Not all of these decorators are traditional wrappers. They are much simplified
from the turbogears 1 decorators, because all they do is register attributes on
the functions they wrap, and then the DecoratedController provides the hooks
needed to support these decorators.

"""
from repoze.what.predicates import NotAuthorizedError
from warnings import warn
from paste.util.mimeparse import best_match
from decorator import decorator

from webob.exc import HTTPUnauthorized, HTTPMethodNotAllowed
from tg.paginate import Page
from tg.configuration import config
from tg.controllers.util import abort
from formencode import variabledecode
from tg import tmpl_context, request, response
from tg.util import partial

from tg.util import Bunch
from tg.flash import flash
#from tg.controllers import redirect

from caching import beaker_cache

class Decoration(object):
    """ Simple class to support 'simple registration' type decorators
    """
    def __init__(self, controller):
        self.controller = controller
        self.engines = {}
        self.engines_keys = []
        self.default_engine = None
        self.custom_engines = {}
        self.render_custom_format = None
        self.validation = None
        self.error_handler = None
        self.hooks = dict(before_validate=[],
                          before_call=[],
                          before_render=[],
                          after_render=[])

    def get_decoration(cls, func):
        try:
            dec = func.decoration
        except:
            dec = func.decoration = cls(func)
        return dec
    get_decoration = classmethod(get_decoration)

    @property
    def exposed(self):
        return bool(self.engines) or bool(self.custom_engines)

    def run_hooks(self, tgl, hook, *l, **kw):
        try:
            syswide_hooks = tgl.config['hooks'][hook]
            for func in syswide_hooks:
                func(*l, **kw)
        except KeyError:
            pass

        for func in self.hooks[hook]:
            func(*l, **kw)

    def wrap_controller(self, tgl, controller):
        controller_callable = controller
        try:
            for wrapper in tgl.config['controller_wrappers']:
                controller_callable = wrapper(self, controller_callable)
        except KeyError:
            pass
        return controller_callable

    def register_template_engine(self, content_type, engine, template,
                                 exclude_names):
        """Registers an engine on the controller.

        Multiple engines can be registered, but only one engine per
        content_type.  If no content type is specified the engine is
        registered at */* which is the default, and will be used
        whenever no content type is specified.

        exclude_names keeps track of a list of keys which will be
        removed from the controller's dictionary before it is loaded
        into the template.  This allows you to exclude some information
        from JSONification, and other 'automatic' engines which don't
        require a template.
        """
        if content_type is None:
            content_type = '*/*'
        self.engines[content_type] = engine, template, exclude_names

        #Avoid engine lookup if we have only one engine registered
        if len(self.engines) == 1:
            self.default_engine = content_type
        else:
            self.default_engine = None

        #this is a work-around to make text/html prominent in respect
        #to other common choices when they have the same weight for
        # paste.util.mimeparse.best_match.
        self.engines_keys = sorted(self.engines.keys(), reverse=True)

    def register_custom_template_engine(self, custom_format, content_type, engine, template,
                                        exclude_names):
        """Registers a custom engine on the controller.

        Multiple engines can be registered, but only one engine per
        custom_format.

        The engine is registered when @expose is used with the
        custom_format parameter and controllers render using this
        engine when the use_custom_format() function is called
        with the corresponding custom_format.

        exclude_names keeps track of a list of keys which will be
        removed from the controller's dictionary before it is loaded
        into the template.  This allows you to exclude some information
        from JSONification, and other 'automatic' engines which don't
        require a template.
        """

        if content_type is None:
            content_type = "*/*"
        self.custom_engines[custom_format] = content_type, engine, template, exclude_names

    def lookup_template_engine(self, tgl):
        """Return the template engine data.

        Provides a convenience method to get the proper engine,
        content_type, template, and exclude_names for a particular
        tg_format (which is pulled off of the request headers).
        """
        request = tgl.request
        response = tgl.response

        if request.response_type and request.response_type in self.engines:
            accept_types = request.response_type
        else:
            accept_types = request.headers.get('accept', '*/*')

        try:
            render_custom_format = request._render_custom_format[self.controller]
        except:
            render_custom_format = self.render_custom_format

        if render_custom_format:
            content_type, engine, template, exclude_names = self.custom_engines[render_custom_format]
        else:
            if self.default_engine:
                content_type = self.default_engine
            elif self.engines:
                content_type = best_match(self.engines_keys, accept_types)
            else:
                content_type = 'text/html'

            # check for overridden content type from the controller call
            try:
                controller_content_type = response.headers['Content-Type']
                # make sure we handle content_types like 'text/html; charset=utf-8'
                content_type = controller_content_type.split(';')[0]
            except KeyError:
                pass

            # check for overridden templates
            try:
                cnt_override_mapping = request._override_mapping[self.controller]
                engine, template, exclude_names = cnt_override_mapping[content_type.split(";")[0]]
            except (AttributeError, KeyError):
                engine, template, exclude_names = self.engines.get(content_type, (None, None, None))


        if 'charset' not in content_type and (content_type.startswith('text') or \
                                              content_type in ('application/xhtml+xml',
                                                               'application/xml',
                                                               'application/json')):
            content_type += '; charset=utf-8'

        return content_type, engine, template, exclude_names

    def register_hook(self, hook_name, func):
        """Registers the specified function as a hook.

        We now have four core hooks that can be applied by adding
        decorators: before_validate, before_call, before_render, and
        after_render. register_hook attaches the function to the hook
        which get's called at the apropriate time in the request life
        cycle.)
        """
        self.hooks[hook_name].append(func)


class _hook_decorator(object):
    """SuperClass for all the specific TG2 hook validators.
    """
    # must be overridden by a particular hook
    hook_name = None

    def __init__(self, hook_func):
        self.hook_func = hook_func

    def __call__(self, func):
        deco = Decoration.get_decoration(func)
        deco.register_hook(self.hook_name, self.hook_func)
        return func


class before_validate(_hook_decorator):
    """A list of callables to be run before validation is performed"""
    hook_name = 'before_validate'


class before_call(_hook_decorator):
    """A list of callables to be run before the controller method is called"""
    hook_name = 'before_call'


class before_render(_hook_decorator):
    """A list of callables to be run before the template is rendered"""
    hook_name = 'before_render'


class after_render(_hook_decorator):
    """A list of callables to be run after the template is rendered.

    Will be run before it is returned returned up the WSGI stack"""

    hook_name = 'after_render'


class expose(object):
    """
    Registers attributes on the decorated function

    :Parameters:
      template
        Assign a template, you could use the syntax 'genshi:template'
        to use different templates.
        The default template engine is genshi.
      content_type
        Assign content type.
        The default content type is 'text/html'.
      exclude_names
        Assign exclude names

    The expose decorator registers a number of attributes on the
    decorated function, but does not actually wrap the function the way
    TurboGears 1.0 style expose decorators did.

    This means that we don't have to play any kind of special tricks to
    maintain the signature of the exposed function.

    The exclude_names parameter is new, and it takes a list of keys that
    ought to be scrubbed from the dictinary before passing it on to the
    rendering engine.  This is particularly usefull for JSON.

    Expose decorator can be stacked like this::

        @expose('json', exclude_names='d')
        @expose('kid:blogtutorial.templates.test_form',
                content_type='text/html')
        @expose('kid:blogtutorial.templates.test_form_xml',
                content_type='text/xml', custom_format='special_xml')
        def my_exposed_method(self):
            return dict(a=1, b=2, d="username")

    The expose('json') syntax is a special case.  json is a buffet
    rendering engine, but unlike others it does not require a template,
    and expose assumes that it matches content_type='application/json'

    If you want to declare a desired content_type in a url, you
    can use the mime-type style dotted notation::

        "/mypage.json" ==> for json
        "/mypage.html" ==> for text/html
        "/mypage.xml" ==> for xml.

    If you're doing an http post, you can also declare the desired
    content type in the accept headers, with standard content type
    strings.

    By default expose assumes that the template is for html.  All other
    content_types must be explicitly matched to a template and engine.

    The last expose uses the custom_format parameter which takes an
    arbitrary value (in this case 'special_xml').  You can then use
    the`use_custom_format` function within the method to decide which
    of the 'custom_format' registered expose decorators to use to
    render the template.
    """

    def __init__(self, template='', content_type=None, exclude_names=None,
                 custom_format=None):
        if exclude_names is None:
            exclude_names = []

        if template in config.get('renderers', []):
            engine, template = template, ''

        elif ':' in template:
            engine, template = template.split(':', 1)

        elif template:
            # Use the default templating engine from the config
            if config.get('use_legacy_renderer'):
                engine = config['buffet.template_engines'][0]['engine']

            else:
                engine = config.get('default_renderer')

        else:
            engine, template = None, None

        if content_type is None:
            if engine == 'json':
                content_type = 'application/json'
            else:
                content_type = 'text/html'

        if (engine == 'json' or engine == 'amf') and 'tmpl_context' not in exclude_names:
            exclude_names.append('tmpl_context')

        self.engine = engine
        self.template = template
        self.content_type = content_type
        self.exclude_names = exclude_names
        self.custom_format = custom_format

    def __call__(self, func):
        deco = Decoration.get_decoration(func)
        if self.custom_format:
            deco.register_custom_template_engine(
                self.custom_format, self.content_type, self.engine,
                self.template, self.exclude_names)
        else:
            deco.register_template_engine(
                self.content_type, self.engine, self.template, self.exclude_names)
        return func


def use_custom_format(controller, custom_format):
    """Use use_custom_format in a controller in order to change
    the active @expose decorator when available."""
    deco = Decoration.get_decoration(controller)

    # Check the custom_format passed is available for use
    if not custom_format in deco.custom_engines.keys():
        raise ValueError("'%s' is not a valid custom_format" % custom_format)

    try:
        render_custom_format = request._render_custom_format
    except AttributeError:
        render_custom_format = request._render_custom_format = {}
    render_custom_format[controller.im_func] = custom_format

def override_template(controller, template):
    """Use overide_template in a controller in order to change the
    template that will be used to render the response dictionary
    dynamically.

    The template string passed in requires that
    you include the template engine name, even if you're using the default.

    So you have to pass in a template id string like::

       "genshi:myproject.templates.index2"

    future versions may make the `genshi:` optional if you want to use
    the default engine.
    """
    try:
        engines = controller.decoration.engines
    except:
        return
    
    for content_type, content_engine in engines.iteritems():
        tmpl = template.split(':', 1)
        tmpl.extend(content_engine[2:])
        try:
            override_mapping = request._override_mapping
        except AttributeError:
            override_mapping = request._override_mapping = {}
        override_mapping.setdefault(controller.im_func, {}).update({content_type: tmpl})

class validate(object):
    """Regesters which validators ought to be applied

    If you want to validate the contents of your form,
    you can use the ``@validate()`` decorator to regester
    the validators that ought to be called.

    :Parameters:
      validators
        Pass in a dictionary of FormEncode validators.
        The keys should match the form field names.
      error_handler
        Pass in the controller method which shoudl be used
        to handle any form errors
      form
        Pass in a ToscaWidget based form with validators

    The first positional parameter can either be a dictonary of validators,
    a FormEncode schema validator, or a callable which acts like a FormEncode
    validator.

    """
    def __init__(self, validators=None, error_handler=None, form=None):
        if form:
            self.validators = form
        if validators:
            self.validators = validators
        self.error_handler = error_handler

    def __call__(self, func):
        deco = Decoration.get_decoration(func)
        deco.validation = self
        return func


class paginate(object):
    """Paginate a given collection.

    This decorator is mainly exposing the functionality
    of :func:`webhelpers.paginate`.

    :Usage:

    You use this decorator as follows::

     class MyController(object):

         @expose()
         @paginate("collection")
         def sample(self, *args):
             collection = get_a_collection()
             return dict(collection=collection)

    To render the actual pager, use::

      ${tmpl_context.paginators.<name>.pager()}

    It is possible to have several :func:`paginate`-decorators for
    one controller action to paginate several collections independently
    from each other. If this is desired, don't forget to set the :attr:`use_prefix`-parameter
    to :const:`True`.

    :Parameters:
      name
        the collection to be paginated.
      items_per_page
        the number of items to be rendered. Defaults to 10
      max_items_per_page
        the maximum number of items allowed to be set via parameter.
        Defaults to 0 (does not allow to change that value).
      use_prefix
        if True, the parameters the paginate
        decorator renders and reacts to are prefixed with
        "<name>_". This allows for multi-pagination.

    """

    def __init__(self, name, use_prefix=False,
        items_per_page=10, max_items_per_page=0):
        self.name = name
        prefix = use_prefix and name + '_' or ''
        self.page_param = prefix + 'page'
        self.items_per_page_param = prefix + 'items_per_page'
        self.items_per_page = items_per_page
        self.max_items_per_page = max_items_per_page

    def __call__(self, func):
        decoration = Decoration.get_decoration(func)
        decoration.register_hook('before_validate', self.before_validate)
        decoration.register_hook('before_render', self.before_render)
        return func

    def before_validate(self, remainder, params):
        page_param = params.pop(self.page_param, None)
        if page_param:
            try:
                page = int(page_param)
                if page < 1:
                    raise ValueError
            except ValueError:
                page = 1
        else:
            page = 1

        try:
            paginators_data = request.paginators
        except:
            paginators_data = request.paginators = {'_tg_paginators_params':{}}

        paginators_data['_tg_paginators_params'][self.page_param] = page_param
        paginators_data[self.name] = paginator = Bunch()

        paginator.paginate_page = page or 1
        items_per_page = params.pop(self.items_per_page_param, None)
        if items_per_page:
            try:
                items_per_page = min(
                    int(items_per_page), self.max_items_per_page)
                if items_per_page < 1:
                    raise ValueError
            except ValueError:
                items_per_page = self.items_per_page
        else:
            items_per_page = self.items_per_page
        paginator.paginate_items_per_page = items_per_page
        paginator.paginate_params = params.copy()
        paginator.paginate_params.update(paginators_data['_tg_paginators_params'])
        if items_per_page != self.items_per_page:
            paginator.paginate_params[self.items_per_page_param] = items_per_page

    def before_render(self, remainder, params, output):
        if not isinstance(output, dict) or not self.name in output:
            return

        paginator = request.paginators[self.name]
        collection = output[self.name]
        page = Page(collection, paginator.paginate_page,
            paginator.paginate_items_per_page, controller='/')
        page.kwargs = paginator.paginate_params
        if self.page_param != 'name':
            page.pager = partial(page.pager, page_param=self.page_param)
        if not getattr(tmpl_context, 'paginators', None):
            tmpl_context.paginators = Bunch()
        tmpl_context.paginators[self.name] = output[self.name] = page


@decorator
def postpone_commits(func, *args, **kwargs):
    """Turns sqlalchemy commits into flushes in the decorated method

    This has the end-result of postponing the commit to the normal TG2
    transaction boundry. """

    #TODO: Test and document this.
    s = config.get('DBSession', None)
    assert hasattr(s, 'commit')
    old_commit = s.commit
    s.commit = s.flush
    retval = func(*args, **kwargs)
    s.commit = old_commit
    return retval

@before_validate
def https(remainder, params):
    '''Ensure that the decorated method is always called with https://'''
    from tg.controllers import redirect
    if request.scheme.lower() == 'https': return
    if request.method.upper() == 'GET':
        redirect('https' + request.url[len(request.scheme):])
    raise HTTPMethodNotAllowed(headers=dict(Allow='GET')).exception

@before_validate
def variable_decode(remainder, params):
    '''Best-effort formencode.variabledecode on the params before validation

    If any exceptions are raised due to invalid parameter names, they are
    silently ignored, hopefully to be caught by the actual validator.  Note that
    this decorator will *add* parameters to the method, not remove.  So for
    instnace a method will move from {'foo-1':'1', 'foo-2':'2'} to
    {'foo-1':'1', 'foo-2':'2', 'foo':['1', '2']}.
    '''
    try:
        new_params = variabledecode.variable_decode(params)
        params.update(new_params)
    except:
        pass

@before_validate
def without_trailing_slash(remainder, params):
    """This decorator allows you to ensure that the URL does not end in "/"
    The decorator accomplish this by redirecting to the correct URL.

    :Usage:

    You use this decorator as follows::

     class MyController(object):

         @without_trailing_slash
         @expose()
         def sample(self, *args):
             return "found sample"

    In the above example http://localhost:8080/sample/ redirects to http://localhost:8080/sample
    In addition, the URL http://localhost:8080/sample/1/ redirects to http://localhost:8080/sample/1
    """
    if request.method == 'GET' and request.path.endswith('/') and not(request.response_type) and len(request.params)==0:
        from tg.controllers import redirect
        redirect(request.url[:-1])


@before_validate
def with_trailing_slash(remainder, params):
    """This decorator allows you to ensure that the URL ends in "/"
    The decorator accomplish this by redirecting to the correct URL.

    :Usage:

    You use this decorator as follows::

     class MyController(object):

         @with_trailing_slash
         @expose()
         def sample(self, *args):
             return "found sample"

    In the above example http://localhost:8080/sample redirects to http://localhost:8080/sample/
    In addition, the URL http://localhost:8080/sample/1 redirects to http://localhost:8080/sample/1/
    """
    if (request.method == 'GET'
        and not(request.path.endswith('/'))
        and not(request.response_type)
        and len(request.params)==0):
        from tg.controllers import redirect
        redirect(request.url+'/')


#{ Authorization decorators
class _BaseProtectionDecorator(object):
    default_denial_handler = None

    def __init__(self, predicate, denial_handler=None):
        """
        Make :mod:`repoze.what` verify that the predicate is met.

        :param predicate: A :mod:`repoze.what` predicate.
        :param denial_handler: The callable to be run if authorization is
            denied (overrides :attr:`default_denial_handler` if defined).

        If called, ``denial_handler`` will be passed a positional argument
        which represents a message on why authorization was denied.

        """
        self.predicate = predicate
        self.denial_handler = denial_handler or self.default_denial_handler

class require(_BaseProtectionDecorator):
    """
    TurboGears-specific repoze.what action protector.

    The default authorization denial handler of this protector will flash
    the message of the unmet predicate with ``warning`` or ``error`` as the
    flash status if the HTTP status code is 401 or 403, respectively.

    See :class:`allow_only` for controller-wide authorization.

    """
    def __call__(self, action_):
        return decorator(self.wrap_action, action_)
    
    def wrap_action(self, action_, *args, **kwargs):
        req = request._current_obj()

        try:
            self.predicate.check_authorization(req.environ)
        except NotAuthorizedError, e:
            reason = unicode(e)
            if req.environ.get('repoze.who.identity'):
                # The user is authenticated.
                code = 403
            else:
                # The user is not authenticated.
                code = 401
            if self.denial_handler:
                response.status = code
                return self.denial_handler(reason)
            abort(code, comment=reason)
        return action_(*args, **kwargs)

    def default_denial_handler(self, reason):
        """Authorization denial handler for repoze.what protectors."""
        if response.status_int == 401:
            status = 'warning'
        else:
            # Status is a 403
            status = 'error'
        flash(reason, status=status)
        abort(response.status_int, reason)

class allow_only(_BaseProtectionDecorator):
    """
    TurboGears-specific repoze.what controller protector.

    The default authorization denial handler of this protector will flash
    the message of the unmet predicate with ``warning`` or ``error`` as the
    flash status if the HTTP status code is 401 or 403, respectively, since
    by default the ``__before__`` method of the controller is decorated with
    :class:`require`.

    If the controller class has the ``_failed_authorization`` *class method*,
    it will replace the default denial handler.

    """
    protector = require

    def __call__(self, cls, *args, **kwargs):
        if hasattr(self.protector, 'predicate'):
            cls.allow_only=self.protector.predicate
        if hasattr(cls, '_failed_authorization'):
            self.denial_handler = cls._failed_authorization
        sup = super(allow_only, self)
        if hasattr(sup, '__call__'):
            return super(allow_only, self).__call__(cls, *args, **kwargs)

class cached_property(object):
    def __init__(self, func):
        self.__name__ = func.__name__
        self.__module__ = func.__module__
        self.__doc__ = func.__doc__
        self.func = func

    def __get__(self, obj, type=None):
        if obj is None:
            return self

        try:
            value = obj.__dict__[self.__name__]
        except KeyError:
            value = obj.__dict__[self.__name__] = self.func(obj)
        return value

#}
