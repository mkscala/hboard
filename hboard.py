#!/usr/bin/python
from flask import Flask, g, url_for, render_template, request
from flask import abort, redirect, after_this_request, send_from_directory
import os
import time
import random
import string
import json
from parsepost import parse_post
import redis
from PIL import Image

from datetime import timedelta
from flask import make_response, request, current_app
from functools import update_wrapper


def crossdomain(origin=None, methods=None, headers=None,
        max_age=21600, attach_to_all=True,
        automatic_options=True):
  if methods is not None:
    methods = ', '.join(sorted(x.upper() for x in methods))
  if headers is not None and not isinstance(headers, basestring):
    headers = ', '.join(x.upper() for x in headers)
  if not isinstance(origin, basestring):
    origin = ', '.join(origin)
  if isinstance(max_age, timedelta):
    max_age = max_age.total_seconds()

  def get_methods():
    if methods is not None:
      return methods

    options_resp = current_app.make_default_options_response()
    return options_resp.headers['allow']

  def decorator(f):
    def wrapped_function(*args, **kwargs):
      if automatic_options and request.method == 'OPTIONS':
        resp = current_app.make_default_options_response()
      else:
        resp = make_response(f(*args, **kwargs))
      if not attach_to_all and request.method != 'OPTIONS':
        return resp

      h = resp.headers

      h['Access-Control-Allow-Origin'] = origin
      h['Access-Control-Allow-Methods'] = get_methods()
      h['Access-Control-Max-Age'] = str(max_age)
      if headers is not None:
        h['Access-Control-Allow-Headers'] = headers
      return resp

    f.provide_automatic_options = False
    return update_wrapper(wrapped_function, f)
  return decorator

FILE_EXTS = [ "jpeg", "jpg", "png", "gif", "webp", "webm" ]
THUMBNAILABLE = [ "jpeg", "jpg", "png", "gif" ]
UPLOAD_FOLDER = "static/img/"
THUMBNAIL_FOLDER = "static/thumbnail/" 

THUMBNAIL_MAX_AXIS_SIZE = 250

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16777216

db = redis.StrictRedis( host='localhost', port=6379, db=0 )

def is_valid_file( fn ):
  return '.' in fn and fn.split( '.' )[-1] in FILE_EXTS

def file_extension( fn ):
  return fn.split( '.' )[-1]

def img( ifn ):
  return url_for( 'static', filename="img/" + ifn )

def get_id( ip ):
  t = int( time.time() / 43200.0 )
  random.seed( ip + str( t ) )
  ch = string.ascii_letters + "-_!" + string.digits
  r = ""
  for i in xrange( 10 ):
    r += random.choice( ch )
  return r

def no_cahce( req ):
  req.headers['Cache-Control'] = 'no-cache'
  return req

def create_thumbnail( img_id ):
  file_path = UPLOAD_FOLDER + img_id 
  thumbnail_path = THUMBNAIL_FOLDER + img_id

  img = Image.open( file_path )
  
  w, h = img.size
  
  # New dimensions
  nw, nh = w, h

  # If the image doesn't need to be scaled down, make the thumbnail just be
  # the original image instead.
  if w > THUMBNAIL_MAX_AXIS_SIZE or h > THUMBNAIL_MAX_AXIS_SIZE:
    # Calcuate the new dimensions so that the greating axis is capped at
    # 250 pixels, and the other axis is scaled accordingly to the ratio
    if w > h:
      nw = THUMBNAIL_MAX_AXIS_SIZE
      nh = int( float( nw ) / w * h ) or 1
    else:
      nh = THUMBNAIL_MAX_AXIS_SIZE
      nw = int( float( nh ) / h * w ) or 1
    # Create the thumbnail
    img.thumbnail( (nw, nh), Image.ANTIALIAS )
  
  img.save( thumbnail_path )

def upload_image( file, b ):
  global db

  if not file:
    return ( False, "No file supplied" )

  if not is_valid_file( file.filename ):
    return ( False, "Invalid file." )

  image_id = db.incr( "imagecounter" )

  pfile = hex( image_id )[2:] + "." + file_extension( file.filename ) 

  db.lpush( "gallery", pfile )
  db.lpush( "gallery:" + b, pfile )

  file.save( UPLOAD_FOLDER + "/" + pfile )

  try:
    if file_extension( file.filename ) in THUMBNAILABLE:
      create_thumbnail( pfile )
  except e:
    print( "Failed to generate thumbnail for '" + pfile + "': " + str( e )  )

  return ( True, pfile )

@app.route("/api/boards/", methods=["GET"])
@crossdomain(origin='*')
def boards_api():
  global db
  return json.dumps( db.hgetall( "boards" ) )

@app.route("/api/boards/<b>/", methods=["GET", "POST"])
@crossdomain(origin='*')
def board_api( b ):
  global db

  boardkey = "board:" + b

  if not db.hexists( "boards", b ):
    abort( 404 )
  
  if request.method == "POST":
    suc, val = upload_image( request.files.get( "file" ), b )
    if suc:
      if request.form.has_key( "text" ):
        post_id = db.incr( boardkey + ":counter" )
        db.hmset( boardkey + ":" + str( post_id )
                , { "poster_id" : get_id( request.remote_addr )
                  , "text" : request.form["text"]
                  , "image" : val
                  , "is_post" : 1 } )
        top_score = db.incr( boardkey + ":top_score" )
        db.zadd( boardkey + ":posts", top_score, post_id )
        return json.dumps( { "post_id" : post_id } )
      else:
        val = "No post text supplied"
    
    return json.dumps( { "error": val } ), 306

  try:
    start = int( request.args.get( "start", 0 ) )
    end = int( request.args.get( "end", -1 ) )
  except:
    return json.dumps( { "error": "Query parameters must be integers" } ), 306

  posts = db.zrange( boardkey + ":posts", start, end )
  
  return json.dumps( posts )

@app.route("/api/boards/<b>/<p>/", methods=["GET", "POST"])
@crossdomain(origin='*')
def post_api( b, p ):
  global db
  
  boardkey = "board:" + b
  postkey = boardkey + ":" + p
  
  if not db.exists( postkey ):
    abort( 404 )

  # Reply to a post
  if request.method == "POST":
    suc, val = (True, None)
    
    if int( db.hget( postkey, "post_id" ) ) == 0:
      return json.dumps( { "error": "Can only reply to posts." } ), 306

    if request.files.has_key( "file" ):
      suc, val = upload_image( request.files["file"], b )

    if not request.form.has_key( "text" ):
      suc, val = (False, "No reply text supplied")
    
    if suc:
      reply_id = db.incr( boardkey + ":counter" )
      db.hmset( boardkey + ":" + str( reply_id )
              , { "poster_id" : get_id( request.remote_addr )
                , "text" : request.form["text"]
                , "image" : val
                , "is_post" : 0 } )
      db.lpush( postkey + ":replies" , reply_id )
      top_score = db.incr( boardkey + ":top_score" )
      db.zadd( boardkey + ":posts", top_score, p )
      return json.dumps( { "reply_id" : reply_id } )
    else:
      return json.dumps( { "error" : val } ), 306

  # View a post
  post = { "poster_id" : db.hget( postkey, "poster_id" )
         , "text" : db.hget( postkey, "text" )
         , "image" : db.hget( postkey, "image" ) }
  
  # Only posts have replies
  if int( db.hget( postkey, "is_post" ) ) == 1:
    post["replies"] = []

    try:
      start = int( request.args.get( "start", 0 ) )
      end = int( request.args.get( "end", -1 ) )
    except:
      return json.dumps( { "error": "Query parameters must be integers" } ), 306

    # Replies are stored as references ( by id )
    reply_ids = db.lrange( postkey + ":replies" , start, end )
    
    # But when calling we want all the replies in-structure and in order
    for reply_id in reply_ids:
      reply = db.hgetall( "board:" + b + ":" + reply_id )
      del reply["is_post"]
      # But we still want to know the id of each post
      reply["id"] = reply_id
      
      post["replies"].append( reply )
  
  return json.dumps( post )

@app.route("/api/gallery/<b>/")
@crossdomain(origin='*')
def api_gallery( b ):
  global db
  try:
    start = int( request.args.get( "start", 0 ) )
    end = int( request.args.get( "end", -1 ) )
  except:
    return json.dumps( { "error": "Query parameters must be integers" } ), 306

  images = db.lrange( "gallery:" + b, start, end )
  return json.dumps( images )

@app.route("/api/gallery/")
@crossdomain(origin='*')
def api_board_gallery():
  global db
  try:
    start = int( request.args.get( "start", 0 ) )
    end = int( request.args.get( "end", -1 ) )
  except:
    return json.dumps( { "error": "Query parameters must be integers" } ), 306

  images = db.lrange( "gallery", start, end )
  
  return json.dumps( images )

@app.route( "/<b>/" )
def board_view( b ):
  if not db.hexists( "boards", b ):
    abort( 404 )
  return send_from_directory( "static", "boardview.html" )

@app.route( "/<b>/<p>" )
def post_view( b, p ):
  if not db.exists( "board:" + b + ":" + p ):
    abort( 404 )
  return send_from_directory( "static", "postview.html" )

@app.route( "/" )
def index():
  return send_from_directory( "static", "index.html" )

if __name__ == "__main__":
  app.debug = True
  app.run( host="0.0.0.0", port=80 )

