"""
This script runs the application using a development server.
It contains the definition of routes and views for the application.
"""

import threading
from flask import (
    Flask, 
    render_template, 
    request,
    redirect,
    url_for,
    flash,
    g,
    session,
    jsonify
    )

import re, os, markupsafe, datetime, random

from better_profanity import profanity 

import auth #type: ignore
from db import get_db #type: ignore
from emailer import send_email #type:ignore

from PIL import Image

DEFAULT_SETTINGS = {
    'theme': 'light',
    'background-image': "galaxy.jpg",
    'header-colour': '#FFFFFF',
    'analytics': True,
    'sorting': 0,
    'allow_dms': True
}

# Notification Templates
NEW_MESSAGE = """\
    <h5>You have a new message from %s!</h5>
"""

# App initialisation
app = Flask(__name__)
app.config.from_mapping(SECRET_KEY='dev', UPLOAD_FOLDER="./static/userUploads/") # We need a secret key to access the session, and an upload folder to put user profile pictures
print(app.config["UPLOAD_FOLDER"])

# Make the WSGI interface available at the top level so wfastcgi can get it.
wsgi_app = app.wsgi_app

# Blueprint for authentication functions
app.register_blueprint(auth.bp)

def get_current_time():
    """ Returns the current datetime formatted for MySQL """

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@app.route('/')
def index():
    """
    Startup
    Renders either welcome page or dashboard
    """

    print(url_for('auth.change_password', email="calhanna@student.pakuranga.school.nz"))

    db = get_db()
    if db.cursor() == False:
        return render_template('oopsie.html')

    return redirect(url_for('dashboard'))

@app.errorhandler(500)
def page_not_found(e):
    return render_template('oopsie.html'), 500

@app.context_processor
def inject():
    """ Inserts additional data into every single page """
    return dict(conversations=fetch_conversations(), user_settings=get_user_settings(), notifications=get_notifications())

def check_likes(cursor, post):
    """ 
        Likes/Dislikes are stored in a table called 'actions'
        We only want to count each user once, and we don't want them to be able to like and dislike at the same time
        Therefore, the only action we care about is the last one performed by each user.
    """

    # Execute SQL to fetch actions
    sql = "SELECT * FROM tblactions WHERE post_id = %s"
    cursor.execute(sql, (post[0]))

    # Convert tuple to array (possibly a no longer necessary artifact of an older implementation, but hey it works)
    actions = list(cursor.fetchall())
    actions.reverse()   # Most recent actions first

    # Create a dict to sort each action into it's user
    user_actions = {}
    for action in actions:
        if action[2] in user_actions:
            user_actions[action[2]].append(action[3])
        else:
            user_actions[action[2]] = [action[3]]
    return user_actions

def fetch_post(post, cursor):
    """ Fetches all the additional information about a post and formats it into a new list """

    user_id = post[1]

    # Fetch the username using the user_id
    # We can either run an SQL check for every post or we can do it in python. I'm doing the SQL check because I think it's easier
    cursor.execute(
        "SELECT * FROM tblusers WHERE ID = %s",
        (user_id)
        )
    user = cursor.fetchone()
    post.append(user[4])

    # Fetch likes/dislikes
    user_actions = check_likes(cursor, post)

    # Figure out what the current user did last. Could do this better with code that has been added after, but I don't care enough to do so
    # The purpose of this is so we can colour the buttons they've already pressed.
    last_action = None
    if g.user is not None:
        last_action = 'none'
        cursor.execute("SELECT * FROM tblactions WHERE post_id = %s AND user_id = %s", (post[0], g.user[0]))
        results = list(cursor.fetchall())
        if results:
            last_action = results[-1] 

    # Tally up the likes/dislikes, only considering the last action of each user
    likes, dislikes = 0, 0
    for user_id in user_actions:
        if user_actions[user_id]:
            a = user_actions[user_id][0]   # I'm sorry for the single character variable but it only exists in this scope ok?
            if a == "Like":
                likes += 1
            else:
                dislikes += 1

    # Escape html
    # Use regex to search for HTML tags, i.e <h1>, <img>, <script>
    #for tag in re.findall(r"<.*>", post[4]):
        #post[4] = post[4].replace(tag, markupsafe.escape(tag))
    post.extend([likes, dislikes, last_action, user[1]])
    return post

def fetch_replies(post, cursor, checked):
    """ Recursive function to fetch the replies to a post and the replies to each reply"""
    if post not in checked:
        checked.append(post)
        cursor.execute(
            "SELECT * FROM tblpost WHERE reply_id = %s",
            (post[0])
        )
        replies = list(cursor.fetchall())
        replies = [list(reply) for reply in replies]
        processed_replies = []
        for reply in replies:
            processed_replies.append(fetch_post(reply, cursor))

        for reply in processed_replies:
            processed_replies.extend(fetch_replies(reply, cursor, checked))

        return processed_replies
    return []

@app.route('/dashboard', methods=["GET", "POST"])
def dashboard():
    """ Fetches posts from database and displays on a feed """
    db = get_db()
    session['url'] = url_for("dashboard")

    with db.cursor() as cursor:

        if g.user is not None:
            # Fetch all posts excluding those made by the current user.
            cursor.execute(
                "SELECT * FROM tblpost WHERE user_id != %s AND reply_id IS Null",
                (g.user[0])
                )
        else:
            cursor.execute("SELECT * FROM tblpost WHERE reply_id IS NULL")
        
        posts = cursor.fetchall()

        posts = [list(post) for post in posts]  # We want to edit the post and tuples can't be edited
        posts.reverse() # Chronological ordering, newest first

        for post in posts:
            
            post = fetch_post(post, cursor)

            # Fetch replies
            checked = []
            replies = fetch_replies(post, cursor, checked)

            post.append(replies)

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM tblusers"
        )
        users = cursor.fetchall()
        username_list = [user[1] for user in users]

    return render_template('/blog/dash.html', posts=posts, username_list = username_list, activated_posts=check_user_likes())

@app.route('/check_user_likes', methods=["GET"])
def check_user_likes():
    if g.user != None:
        sql = "SELECT * FROM tblactions WHERE user_id = %s"

        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(sql, (g.user[0]))
            results = cursor.fetchall()

        liked_posts = [x[1] for x in results if x[3] == "Like"]
        disliked_posts = [x[1] for x in results if x[3] == "Dislike"]

        #return jsonify({'liked_posts': liked_posts, 'disliked_posts': disliked_posts})

        return (liked_posts, disliked_posts)

@app.route('/add_like', methods=['POST'])
def add_likes():
    """ Function for adding likes to posts by creating entry in tblactions. Slight misnomer, this function also handles dislikes"""

    if request.method == 'POST':
        if g.user is None:
            # If ur not signed in, go away
            return jsonify({
                'success': False
            })

        db = get_db()
        post_id = request.form['post_id']
        action = request.form['like']
        
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM tblactions WHERE user_id = %s AND post_id = %s", (g.user[0], post_id))
            results = cursor.fetchall()
            if len(results) == 0:
                # Create entry
                sql = "INSERT INTO tblactions (post_id, user_id, type) VALUES (%s, %s, '" + action + "')"
            else:
                if results[0][3] == action:
                    # Delete entry
                    sql = "DELETE FROM tblactions WHERE post_id = %s and user_id = %s"
                else:
                    # Update entry
                    sql = "UPDATE tblactions SET `type`='"+ action +"' WHERE post_id = %s and user_id = %s" 

            cursor.execute(
                sql,
                (post_id, g.user[0])
                )
            db.commit()

            cursor.execute('SELECT * FROM tblactions WHERE post_id = %s AND type = "Like"', (post_id))
            like_count = len(cursor.fetchall())
            cursor.execute("SELECT * FROM tblactions WHERE post_id = %s AND type = 'Dislike'", (post_id))
            dislike_count = len(cursor.fetchall())

        return jsonify(
            {
                'success': True,
                'like_count': like_count,
                'dislike_count': dislike_count
            }
        )

@app.route("/create_post", methods=["GET", "POST"])
def create_post():
    if request.method == "POST":

        if not g.user[6]:
            return jsonify({'error': "email not confirmed"})

        content = request.form["post_content"]
        stripped_content = request.form["stripped_content"].strip().strip('\n') # just the text
        reply_id = request.form["reply_id"]
        if reply_id == '0': reply_id = None

        db = get_db()

        error = None
        
        if not content.strip().strip('\n').replace('<p>', '').replace('<br>','').replace('</p>', ''):  # Remove html tags to count the actual post length
            error = "Post body required" 
        elif len(stripped_content) > 255:
            error = "Post length over limit"
        else:
            cursor = db.cursor()
            now = datetime.datetime.now()

            cursor.execute(
                " INSERT INTO tblpost (user_id, date, time, content, reply_id) VALUES (%s, %s, %s, %s, %s)",
                (g.user[0], now.date(), now.time(), content, reply_id)
                )
            db.commit()
            print('done')

        return jsonify({'error': error})

@app.route("/delete_post/<post_id>", methods=["GET", "POST"])
def delete_post(post_id):
    db = get_db()
    
    with db.cursor() as cursor:
        cursor.execute(
            "DELETE FROM tblpost WHERE post_id = %s",
            (post_id)
        )
        db.commit()

    return redirect(session['url'])

@app.route("/profile/<user>", methods = ["GET", "POST"])
def profile(user):
    db = get_db()

    if g.user is None:
        return redirect(url_for('dashboard'))

    user = int(user)

    session['url'] = url_for("profile", user=user)

    cursor = db.cursor()

    cursor.execute(
        "SELECT * FROM tblpost WHERE user_id = %s",
        (user)
    )
    posts = cursor.fetchall()
    posts = [list(post) for post in posts]  # We want to edit the post and tuples can't be edited
    posts.reverse() # Chronological ordering, newest first
    for post in posts:
        post = fetch_post(post, cursor)

        # Fetch replies
        checked = []
        replies = fetch_replies(post, cursor, checked)

        post.append(replies)

    if request.method=="POST":
        # Upload user profile_picture
        file = request.files['pfp']
        # If the user does not select a file, the browser submits an
        # empty file without a filename.
        if file.filename == '':
            print('No selected file')
            return redirect(request.url)
        if file and allowed_file(file.filename):
            upload_folder = app.config['UPLOAD_FOLDER'].replace("./static/", "./")
            n = "%s.%s" % (g.user[0], file.filename.split('.')[-1])

            file.save(os.path.join(app.config['UPLOAD_FOLDER'], n))
            cursor.execute(
                "UPDATE tblusers SET `profile_picture` = %s WHERE ID = %s",
                (os.path.join(upload_folder, n), g.user[0])
                )
            db.commit()

            return redirect(url_for("profile", user=g.user[0]))
        
    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM tblusers WHERE id = %s", (user))
        user_info = cursor.fetchone()
    
    return render_template('/blog/profile.html', posts=posts, activated_posts=check_user_likes(), user=user, user_info=user_info)

@app.route("/report", methods=["POST"])
def report():
    user = request.form['user_id']
    reported_user = request.form['reported_user']
    reason = request.form['reason']

    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO tblreport (user_id, reported_user, reason) VALUES (%s, %s, %s)", (user, reported_user, reason))
    db.commit()
    return jsonify({'success': True})

def fetch_conversations():
    """ Fetches all of a user's active conversations """

    if g.user is None:
        return []

    db = get_db()

    # Step 0: Remove conversations with only one user
    with db.cursor() as cursor:
        cursor.execute("""
        DELETE FROM
            tblconversations 
        WHERE
            convo_id IN ( SELECT convo_id FROM ( SELECT convo_id, COUNT(*) AS user_count FROM tblconvo_users GROUP BY convo_id ) AS a WHERE user_count <= 1 );
        """)
        db.commit()

    # Step 1: fetch conversations
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM tblconversations WHERE tblconversations.convo_id IN (SELECT convo_id FROM tblconvo_users WHERE user_id = %s)
            """,
            (g.user[0])
        )

        conversations = dict.fromkeys(cursor.fetchall())

    # Step 2: Fetch all the messages in each conversation
    for conversation in conversations.keys():
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM tblmessages INNER JOIN tblusers ON tblmessages.sender_id = tblusers.id WHERE convo_id = %s", (conversation[0]))
            conversations[conversation] = cursor.fetchall()

    # Step 3: Order conversations by most recent message
    conversations = list(conversations.items())
    try:
        conversations.sort(key = lambda message: message[1][-1][4])
        conversations.reverse()
    except IndexError as e:
        # Conversation is empty
        print(e)
        pass
    
    # Step 4: Add participants to data structure
    conversations = [list(x) for x in conversations]
    for conversation in conversations:
        print(len(conversation))
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM tblusers WHERE tblusers.id IN (SELECT user_id FROM tblconvo_users WHERE convo_id = %s )", [conversation[0][0]])
            conversation.append(cursor.fetchall())

    # Final conversation structure: [[actual conversation data], [messages], [participants]]

    return conversations

@app.route('/create_conversation', methods=["POST"])
def create_conversation():
    """ Adds a new conversation to tblconversations and adds the appropriate users to tblconvo_users"""

    db = get_db()

    if not g.user[6]:
        return jsonify({'success': False})

    recipitent_id = request.form['recipitent_id']
    convo_type = request.form['type']

    dt = get_current_time()
    with db.cursor() as cursor:

        sql = """
            SELECT
                convo_id 
            FROM
                tblconversations
            WHERE
                convo_id IN (
                SELECT
                    tblconvo_users.convo_id
                FROM
                    tblconvo_users
                INNER JOIN tblconversations ON
                    tblconversations.convo_id = tblconvo_users.convo_id
                WHERE
                    tblconvo_users.user_id = %s)
                AND convo_id IN (
                SELECT
                    tblconvo_users.convo_id
                FROM
                    tblconvo_users
                INNER JOIN tblconversations ON
                    tblconversations.convo_id = tblconvo_users.convo_id
                WHERE
                    tblconvo_users.user_id = %s)
        """
        cursor.execute(sql, (g.user[0], int(recipitent_id)))
        if len(cursor.fetchall()) > 0:
            return jsonify({'success': False})

        cursor.execute("INSERT INTO tblconversations (date, type) VALUES (%s, '" + convo_type + "')", (dt))
        db.commit()

        cursor.execute("SELECT username FROM tblusers WHERE id = %s", (recipitent_id))
        recipitent = cursor.fetchone()[0]

        cursor.execute("SELECT convo_id FROM tblconversations")   # Getting the autoincrement id number of the conversation we just made
        id_number = cursor.fetchall()[-1][0]

        # Add users to conversation using tblconvo_users
        cursor.execute("INSERT INTO tblconvo_users (user_id, convo_id) VALUES (%s, %s)", (g.user[0], id_number))
        cursor.execute("INSERT INTO tblconvo_users (user_id, convo_id) VALUES (%s, %s)", (int(recipitent_id), id_number))
        db.commit()

    # We send this html back to the browser to display the new conversation without reloading
    html = """
    <div class="card"> 
        <div class="card-title convo-title">
            <h5>%s</h3>
        </div>
        <div class="card-body convo-body">
        </div>
        <div class="card-footer">
            <div class="messager-form %s" id="messenger-%s" action="#">
                <input type="text" name="message-body" />
                <button type="submit" class="convo-submit" > <i class="fa fa-paper-plane"></i></button>
            </div>
        </div>
    </div>
    """ % (recipitent, id_number, id_number)

    return jsonify({'success': True, 'html': markupsafe.Markup(html)})

@app.route('/send_message', methods=["POST"])
def send_message():
    """ Creates new message in tblmessages with appropriate values """

    if not g.user[6]:
        return jsonify({'html': "nice try"})

    message_body = request.form['message_body']
    convo_id = request.form['convo_id']

    timestamp = get_current_time()

    db = get_db()
    with db.cursor() as cursor:
        message_body = profanity.censor(message_body)
        cursor.execute("INSERT INTO tblmessages (sender_id, convo_id, message, timestamp) VALUES (%s, %s, %s, %s)", (g.user[0], convo_id, message_body, timestamp))
        db.commit()

    message_html = """
    <div class="message message-%s outgoing">
        <div class="message-body">%s</div>
        <img class="pfp" src="%s" />
    </div>
    """ % (convo_id, message_body, url_for('static', filename=g.user[4].strip('.')))

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM tblusers INNER JOIN tblconvo_users ON tblusers.id = tblconvo_users.user_id WHERE convo_id = %s AND id <> %s", (convo_id, g.user[0]))
        for user in cursor.fetchall():
            send_notification(user, NEW_MESSAGE % (g.user[1]))

    return jsonify({'html': message_html})

@app.route('/refresh_conversation', methods=["GET"])
def refresh_conversation():
    pass

@app.route('/update_post', methods=["POST"])
def update_post():
    content = request.form['post_content']
    post_id = request.form['post_id']
    sql = "UPDATE tblpost SET content = %s WHERE post_id = %s"

    db = get_db()
    with db.cursor() as cursor:
        content = profanity.censor(content)
        cursor.execute(sql, (content, post_id))

    db.commit()

    return jsonify({'content':content})

@app.route('/get_username_list', methods=["POST"])
def get_username_list():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT username FROM tblusers"
        )
        username_list = [x[0] for x in cursor.fetchall()]
        return jsonify({'username_list': str(username_list)})

@app.route('/search', methods=["GET", "POST"])
def search():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM tblusers"
        )
        users = cursor.fetchall()
        username_list = [user[1] for user in users]

    try:
        query = "%{0}%".format(request.form['query'])
    except KeyError:
        # I don't know how this happens, and I don't want to know.
        return redirect(url_for('dashboard'))

    # This is almost the same as the dashboard post displaying code.
    with db.cursor() as cursor:
        cursor.execute(
            'SELECT * FROM tblpost WHERE content like "{0}";'.format(query)
        )
        posts = cursor.fetchall()
    
        posts = [list(post) for post in posts]  # We want to edit the post and tuples can't be edited
        posts.reverse()

        for post in posts:
            
            post = fetch_post(post, cursor)

            # Fetch replies
            checked = []
            replies = fetch_replies(post, cursor, checked)

            post.append(replies)

    return render_template('/search.html', posts=posts, activated_posts=check_user_likes())

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ["png", "jpg", "jpeg", "jfif", "webp"]

@app.route('/options', methods=["GET", "POST"])
def options():
    """ Manages user options, checking against the default values and saving to tblsettings """

    if g.user is None:
        return redirect(url_for('dashboard'))

    db = get_db()
    if request.method == "POST":
        if request.form['id'] != 'account':
            user_settings = {}
            for key in request.form.keys():
                if key == 'id':
                    continue
                # Check that this actually changes from the default settings. We only want to store the changes we have to in order to save space. The second condition is required to allow the user to switch back to the default settings
                if request.form[key] != DEFAULT_SETTINGS[key] or (key in get_user_settings().keys() and request.form[key] != get_user_settings()[key]):
                    user_settings[key] = request.form[key]

            with db.cursor() as cursor:
                for key in user_settings.keys():
                    sql = """
                        INSERT INTO `tblsettings` (`user_id`, `key`, `value`) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE `value` = %s
                    """     # Insert a record if the setting hasn't been changed before, otherwise update the old record.
                    cursor.execute(sql, (g.user[0], key, user_settings[key], user_settings[key]))
                db.commit()
        else:
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE tblusers SET `username`=%s WHERE id = %s", (request.form['username'], g.user[0])
                )
                db.commit()

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM tblusers"
        )
        users = cursor.fetchall()
        username_list = [user[1] for user in users]

    return render_template('options.html', username_list = username_list)

def get_user_settings():
    """ Returns a dictionary containing all of the user's current settings. If they haven't changed a setting, we use it's default value. """

    if g.user == None:
        return DEFAULT_SETTINGS

    db = get_db()
    with db.cursor() as cursor:
        sql = """
            SELECT `key`, `value` FROM tblsettings WHERE user_id = %s
        """
        cursor.execute(sql, g.user[0])
        user_settings = dict(cursor.fetchall())
    
    for key in DEFAULT_SETTINGS.keys():
        if key not in user_settings.keys():
            user_settings[key] = DEFAULT_SETTINGS[key]

    return user_settings

def get_notifications():
    if g.user == None:
        return []
    
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("SELECT id, message FROM tblnotifications WHERE user_id = %s", (g.user[0]))
        return cursor.fetchall()

def send_notification(user, message):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO tblnotifications (user_id, message) VALUES (%s, %s)", (user[0], message))
        db.commit()

    em_thread = threading.Thread(target=send_email, args=(user[3], "New notification from Twitblr", message))
    em_thread.start()

@app.route('/destroy_notification', methods=["POST"])
def destroy_notification():
    notif_id = request.form['notif_id']
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM tblnotifications WHERE id = %s", (notif_id))
        db.commit()
    return jsonify({})

@app.route('/delete_account', methods=["GET"])
def delete_account():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM tblusers WHERE id = %s", (g.user[0]))
        db.commit()

    return redirect(url_for('index'))

@app.route("/confirm_email")
def confirm_email():
    """ Changes a 1 to a 0. Has no other effect """

    db = get_db()
    if g.user is None: 
        return redirect(url_for('dashboard'))

    with db.cursor() as cursor:
        cursor.execute("UPDATE tblusers SET `confirmed`=1 WHERE email = %s", (g.user[3]))
        db.commit()

    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    import os
    HOST = os.environ.get('SERVER_HOST', 'localhost')
    try:
        PORT = int(os.environ.get('SERVER_PORT', '5555'))
    except ValueError:
        PORT = 5555
    app.run(HOST, PORT)
