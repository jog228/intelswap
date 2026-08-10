from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session, g, send_from_directory
import psycopg2
from db_helper import get_db_connection, execute_query, execute_insert
import os
from flask_login import LoginManager, UserMixin, login_required, current_user, login_user, logout_user
import uuid
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder = 'templates', static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-me')

UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads'))
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'zip', 'csv'}
MAX_FILE_SIZE = 16 * 1024 * 1024

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

os.makedirs(UPLOAD_FOLDER, exist_ok = True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view ='login'

flaskenv = os.environ.get('FLASK_ENV', 'development')

class User(UserMixin):
    def __init__(self, m1id=None, user_id=None, username=None, full_name=None):
        self.m1id = m1id
        self.id = user_id
        self.username = username
        self.full_name = full_name

@login_manager.user_loader
def load_user(user_id: str):
    user_data = execute_query("SELECT * FROM users WHERE id = %s", (user_id,), fetchone=True)
    if user_data:
        return User(m1id=user_data['username'], 
            user_id=user_data['id'],
            username=user_data['username'], 
            full_name=user_data['full_name']
        )
    return None

def get_or_create_user(username: str):
    """Get user from database or create if it doesn't exist"""
    query = "SELECT * FROM users WHERE username = %s"
    user_data = execute_query(query, (username,), fetchone=True)
    if user_data:
        return User(m1id=username, user_id=user_data['id'], username=user_data['username'], full_name=user_data.get('full_name'))
    else:
        email = f"{username}@example.come"
        insert_query = """
            INSERT INTO users (username, email, full_name)
            VALUES (%s, %s, %s)
            RETURNING id, username, full_name
        """
        result = execute_insert(insert_query, (username, email, username))
        if result:
            return User(m1id=username, user_id=result['id'], username=result['username'], full_name=result.get('full_name'))
        return None

def get_usr() -> str:
    """Get current authenticated user's m1id"""
    if current_user and current_user.is_authenticated:
        return current_user.m1id
    return 'not authenticated'

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        if not username:
            flash('Enter a username', 'error')
            return render_template('login.html')
        user = get_or_create_user(username)
        if user:
            login_user(user)
            return redirect(url_for('home'))
        flash('Could not log in', 'error')
    demo_users = execute_query("SELECT username, full_name FROM users ORDER BY username", fetchall=True) or []
    return render_template('login.html', demo_users=demo_users)

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/')
def home():
    """Home page with all posts"""
    post_type = request.args.get('type', 'all')
    answered = request.args.get('answered', 'all')
    dataset_id = request.args.get('dataset', None)
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    query = """
        SELECT
            p.*,
            u.username,
            u.full_name,
            COUNT(c.id) as comment_count,
            MAX(CASE WHEN c.is_accepted = TRUE THEN 1 ELSE 0 END) as has_accepted_answer,
            d.name as dataset_name,
            d.slug as dataset_slug
        FROM posts p
        JOIN users u ON p.author_id = u.id
        LEFT JOIN comments c ON p.id = c.post_id
    """
    conditions = ["p.is_archived = FALSE"]
    params = []
    if dataset_id:
        try:
            query += " JOIN post_datasets pd ON p.id = pd.post_id"
            query += " LEFT JOIN datasets d ON pd.dataset_id = d.id"
            conditions.append("pd.dataset_id = %s")
            params.append(int(dataset_id))
        except ValueError:
            query += " LEFT JOIN post_datasets pd ON p.id = pd.post_id"
            query += " LEFT JOIN datasets d ON pd.dataset_id = d.id"
    else:
        query += " LEFT JOIN post_datasets pd ON p.id = pd.post_id"
        query += " LEFT JOIN datasets d ON pd.dataset_id = d.id"
    
    if post_type == 'question':
        conditions.append("p.is_question = TRUE")
    elif post_type == 'post':
        conditions.append("p.is_question = FALSE")
    
    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)
    
    group_by = """
        GROUP BY p.id, p.title, p.body, p.author_id, p.is_question,
            p.created_at, p.updated_at, p.view_count,
            u.username, u.full_name, d.name, d.slug
    """

    having_clause = ""
    if post_type == 'question' and answered in ['answered', 'unanswered']:
        if answered == 'answered':
            having_clause = " HAVING MAX(CASE WHEN c.is_accepted = TRUE THEN 1 ELSE 0 END) = 1"
        else:
            having_clause = " HAVING MAX(CASE WHEN c.is_accepted = TRUE THEN 1 ELSE 0 END) = 0"
    
    query = query + where_clause + group_by + having_clause + " ORDER BY p.created_at DESC"
    
    count_query = f"SELECT COUNT(*) as total FROM ({query}) as subquery"
    count_result = execute_query(count_query, tuple(params) if params else None, fetchone=True)
    total_posts = count_result['total'] if count_result else 0
    
    paginated_query = query + " LIMIT %s OFFSET %s"
    paginated_params = list(params) + [per_page, offset]
    posts = execute_query(paginated_query, tuple(paginated_params), fetchall=True)
    
    if posts:
        post_ids = [post['id'] for post in posts]
        placeholders = ','.join(['%s'] * len(post_ids))
        tags_query = f"""
            SELECT pt.post_id, t.id, t.name, t.slug
            FROM tags t
            INNER JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id IN ({placeholders})
            ORDER BY pt.post_id, t.name
        """
        all_tags = execute_query(tags_query, tuple(post_ids), fetchall=True)
        
        tags_by_post = {}
        for tag in all_tags:
            post_id = tag['post_id']
            if post_id not in tags_by_post:
                tags_by_post[post_id] = []
            tags_by_post[post_id].append({
                'id': tag['id'],
                'name': tag['name'],
                'slug': tag['slug']
            })
        
        for post in posts:
            post['tags'] = tags_by_post.get(post['id'], [])
    
    selected_dataset = None
    if dataset_id:
        try:
            selected_dataset = execute_query(
                "SELECT * FROM datasets WHERE id = %s", 
                (int(dataset_id),), 
                fetchone=True
            )
        except (ValueError, Exception):
            pass
    
    total_pages = (total_posts + per_page - 1) // per_page

    return render_template('home.html', posts=posts, post_type=post_type, answered=answered, selected_dataset=selected_dataset, page=page, total_pages=total_pages)

@app.route('/posts/new', methods=['GET', 'POST'])
@login_required
def new_post():
    """Display new post form and handle creation"""
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        is_question = request.form.get('is_question') == 'on'
        dataset_id = request.form.get('dataset_id', None)
        tags_id = request.form.getlist('tags')
        if not title:
            flash('Title is required', 'error')
            return render_template('posts/new_post.html')
        if not body:
            flash('Body is required', 'error')
            return render_template('posts/new_post.html')
        if len(tags_id) > 10:
            flash('Maximum 10 tags allowed per post', 'error')
            return redirect(url_for('new_post'))
        query = """
            INSERT INTO posts (title, body, author_id, is_question)
            VALUES (%s,%s, %s, %s)
            RETURNING id
        """
        result = execute_insert(query, (title, body, current_user.id, is_question))
        if result:
            post_id = result['id']
            if dataset_id and dataset_id.strip():
                try:
                    dataset_query = """
                        INSERT INTO post_datasets (post_id, dataset_id)
                        VALUES(%s, %s)
                    """
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(dataset_query, (post_id, int(dataset_id)))
                        conn.commit()
                        cursor.close()
                except Exception as e:
                    print(f"Error adding dataset: {e}")
            if tags_id:
                try:
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        for tag_id in tags_id:
                            if tag_id:
                                cursor.execute("""
                                    INSERT INTO post_tags (post_id, tag_id)
                                    VALUES (%s, %s)
                                """, (post_id, int(tag_id)))
                        conn.commit()
                        cursor.close()
                except Exception as e:
                    print(f"Error adding tags: {e}")
            snippet_count = 0
            while f'code_content_{snippet_count}' in request.form:
                code_content = request.form.get(f'code_content_{snippet_count}', '').strip()
                if code_content:
                    code_title = request.form.get(f'code_title_{snippet_count}', '').strip()
                    code_language = request.form.get(f'code_language_{snippet_count}', 'text')
                    code_query = """
                        INSERT INTO code_snippets (post_id, title, code, language)
                        VALUES (%s, %s, %s, %s)
                    """
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(code_query, (post_id, code_title, code_content, code_language))
                        conn.commit()
                        cursor.close()
                snippet_count += 1
            if 'attachments' in request.files:
                files = request.files.getlist('attachments')
                for file in files:
                    if file and file.filename and allowed_file(file.filename):
                        original_filename = secure_filename(file.filename)
                        unique_filename = f"{uuid.uuid4()}_{original_filename}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        file.save(filepath)
                        file_size = os.path.getsize(filepath)
                        attachments_query = """
                            INSERT INTO attachments (post_id, filename, original_filename, file_size, mime_type, uploaded_by)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            RETURNING id
                        """
                        execute_insert(attachments_query, (post_id, unique_filename, original_filename, file_size, file.content_type, current_user.id))
            flash('Post created successfully!', 'success')
            return redirect(url_for('view_post', id=result['id']))
        else:
            flash('Error creating post', 'error')
            return render_template('posts/new_post.html')
    return render_template('posts/new_post.html')

@app.route('/posts/<int:id>')
def view_post(id):
    """View a single post"""
    query = """
        SELECT
            p.*,
            u.username,
            u.full_name
        FROM posts p
        JOIN users u ON p.author_id = u.id
        WHERE p.id = %s
    """
    post = execute_query(query, (id,), fetchone=True)
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    if post.get('is_archived', False):
        if not current_user.is_authenticated or current_user.id != post['author_id']:
            from flask import abort
            abort(404)
    update_query = "UPDATE posts SET view_count = view_count + 1 WHERE id = %s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(update_query, (id,))
        conn.commit()
        cursor.close()
    datasets_query = """
        SELECT d.id, d.name, d.slug, d.url
        FROM datasets d
        JOIN post_datasets pd ON d.id = pd.dataset_id
        WHERE pd.post_id = %s
    """
    post_datasets = execute_query(datasets_query, (id,), fetchall=True)
    tags_query = """
        SELECT t.id, t.name, t.slug
        FROM tags t
        INNER JOIN post_tags pt ON t.id = pt.tag_id
        WHERE pt.post_id = %s
        ORDER BY t.name
    """
    post_tags = execute_query(tags_query, (id,), fetchall=True)
    code_snippets_query = """
        SELECT * FROM code_snippets
        WHERE post_id = %s
        ORDER BY created_at ASC
    """
    code_snippets = execute_query(code_snippets_query, (id,), fetchall=True)
    attachments_query = """
        SELECT a.*, u.username, u.full_name
        FROM attachments a
        JOIN users u ON a.uploaded_by = u.id
        WHERE a.post_id = %s
        ORDER BY a.uploaded_at ASC
    """
    attachments = execute_query(attachments_query, (id,), fetchall=True)
    comments_query = """
        SELECT
            c.*,
            u.username,
            u.full_name
        FROM comments c
        JOIN users u ON c.author_id = u.id
        WHERE c.post_id = %s
        ORDER BY c.is_accepted DESC, c.created_at ASC
    """
    comments = execute_query(comments_query, (id,), fetchall=True)
    comment_attachments_query = """
        SELECT ca.*, u.username, u.full_name
        FROM comment_attachments ca
        JOIN users u ON ca.uploaded_by = u.id
        WHERE ca.comment_id = ANY(
            SELECT c.id FROM comments c WHERE c.post_id = %s
        )
        ORDER BY ca.uploaded_at ASC
    """
    comment_attachments = execute_query(comment_attachments_query, (id,), fetchall=True)
    comment_attachments_dict = {}
    for attachment in comment_attachments:
        comment_id = attachment['comment_id']
        if comment_id not in comment_attachments_dict:
            comment_attachments_dict[comment_id] = []
        comment_attachments_dict[comment_id].append(attachment)
    comment_code_snippets_query = """
        SELECT ccs.*
        FROM comment_code_snippets ccs
        WHERE ccs.comment_id IN (
            SELECT c.id FROM comments c WHERE c.post_id = %s
        )
        ORDER BY ccs.created_at ASC
    """
    comment_code_snippets = execute_query(comment_code_snippets_query, (id,), fetchall=True)
    comment_code_snippets_dict = {}
    for snippet in comment_code_snippets:
        comment_id = snippet['comment_id']
        if comment_id not in comment_code_snippets_dict:
            comment_code_snippets_dict[comment_id] = []
        comment_code_snippets_dict[comment_id].append(snippet)
    comments_dict = {}
    root_comments = []
    for comment in comments:
        comment['replies'] = []
        comment['attachments'] = comment_attachments_dict.get(comment['id'], [])
        comment['code_snippets'] = comment_code_snippets_dict.get(comment['id'], [])
        comments_dict[comment['id']] = comment
    for comment in comments:
        if comment['parent_comment_id'] is None:
            root_comments.append(comment)
        else:
            parent = comments_dict.get(comment['parent_comment_id'])
            if parent:
                parent['replies'].append(comment)
    is_author = current_user.is_authenticated and current_user.id == post['author_id']
    is_bookmarked = False
    if current_user.is_authenticated:
        bookmark_check = execute_query(
            "SELECT id FROM bookmarks WHERE user_id = %s AND post_id = %s",
            (current_user.id, id),
            fetchone=True
        )
        is_bookmarked = bookmark_check is not None
    return render_template('posts/view_post.html', post=post, comments=root_comments, attachments=attachments, code_snippets=code_snippets, is_author=is_author, post_datasets=post_datasets, post_tags=post_tags, is_bookmarked=is_bookmarked)

@app.route('/posts/<int:post_id>/comments', methods=['POST'])
@login_required
def add_comment(post_id):
    """Add a comment to a post"""
    body = request.form.get('body', '').strip()
    parent_comment_id = request.form.get('parent_comment_id', None)
    if not body:
        flash('Comment cannot be empty', 'error')
        return redirect(url_for('view_post', id=post_id))
    parent_id = None
    if parent_comment_id and parent_comment_id.strip():
        try:
            parent_id = int(parent_comment_id)
        except (ValueError, TypeError):
            parent_id = None
    query = """
        INSERT INTO comments(post_id, parent_comment_id, author_id, body)
        VALUES (%s,%s, %s, %s)
        RETURNING id
    """
    result = execute_insert(query, (post_id, parent_id, current_user.id, body))
    if result:
        comment_id = result['id']

        snippet_count = 0
        while f'comment_code_content_{snippet_count}' in request.form:
            code_content = request.form.get(f'comment_code_content_{snippet_count}', '').strip()
            if code_content:
                code_title = request.form.get(f'comment_code_title_{snippet_count}', '').strip()
                code_language = request.form.get(f'comment_code_language_{snippet_count}', 'text')
                code_query = """
                    INSERT INTO comment_code_snippets (comment_id, title, code, language)
                    VALUES (%s, %s, %s, %s)
                """
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(code_query, (comment_id, code_title, code_content, code_language))
                    conn.commit()
                    cursor.close()
            snippet_count += 1

        if 'comment_attachments' in request.files:
            files = request.files.getlist('comment_attachments')
            for file in files:
                if file and file.filename and allowed_file(file.filename):    
                    original_filename = secure_filename(file.filename)
                    unique_filename = f"{uuid.uuid4()}_{original_filename}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(filepath)
                    file_size = os.path.getsize(filepath)
                    attachments_query = """
                        INSERT INTO comment_attachments (comment_id, filename, original_filename, file_size, mime_type, uploaded_by)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """
                    execute_insert(attachments_query, (comment_id, unique_filename, original_filename, file_size, file.content_type, current_user.id))
        flash('Comment added successfully', 'success')
    else:
        flash('Error adding comment', 'error')
    return redirect(url_for('view_post', id=post_id))

@app.route('/posts/<int:post_id>/comments/<int:comment_id>/edit', methods=['POST'])
@login_required
def edit_comment(post_id, comment_id):
    """Edit a comment's body (only the author of the comment)"""
    comment = execute_query(
        "SELECT * FROM comments WHERE id = %s AND post_id = %s",
        (comment_id, post_id),
        fetchone=True
    )
    if not comment:
        flash('Comment not found', 'error')
        return redirect(url_for('view_post', id=post_id))
    if current_user.id != comment['author_id']:
        flash('Only the comment author can edit this', 'error')
        return redirect(url_for('view_post', id=post_id))
    new_body = request.form.get('body', '').strip()
    if not new_body:
        flash('Comment cannot be empty', 'error')
        return redirect(url_for('view_post', id=post_id))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE comments SET body = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND author_id = %s",
            (new_body, comment_id, current_user.id)
        )
        conn.commit()
        cursor.close()
    flash('Comment updated successfully', 'success')
    return redirect(url_for('view_post', id=post_id))

@app.route('/posts/<int:post_id>/comments/<int:comment_id>/accept', methods=['POST'])
@login_required
def accept_comment(post_id, comment_id):
    """Mark a comment as accepted answer (only for question authors)"""
    post_query = "SELECT * FROM posts WHERE id = %s AND is_question = TRUE"
    post = execute_query(post_query, (post_id,), fetchone=True)
    if not post:
        flash('Post/question not found', 'error')
        return redirect(url_for('view_post', id=post_id))
    if current_user.id != post['author_id']:
        flash('Only the question author can accept answers', 'error')
        return redirect(url_for('view_post', id=post_id))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE comments SET is_accepted = FALSE WHERE post_id = %s",
            (post_id,)
        )
        cursor.execute(
            "UPDATE comments SET is_accepted = TRUE WHERE id = %s AND post_id = %s",
            (comment_id, post_id)
        )
        conn.commit()
        cursor.close()
        flash('Answer accepted', 'success')
        return redirect(url_for('view_post', id=post_id))

@app.route('/posts/<int:post_id>/comments/<int:comment_id>/unaccept', methods=['POST'])
@login_required
def unaccept_comment(post_id, comment_id):
    """Unmark a comment as accepted answer (only for question authors)"""
    post_query = "SELECT * FROM posts WHERE id = %s AND is_question = TRUE"
    post = execute_query(post_query, (post_id,), fetchone=True)
    if not post:
        flash('Post/question not found', 'error')
        return redirect(url_for('view_post', id=post_id))
    if current_user.id != post['author_id']:
        flash('Only the question author can unmark answers', 'error')
        return redirect(url_for('view_post', id=post_id))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE comments SET is_accepted = FALSE WHERE id = %s AND post_id = %s",
            (comment_id, post_id)
        )
        conn.commit()
        cursor.close()
    flash('Answer unmarked successfully', 'success')
    return redirect(url_for('view_post', id=post_id))

@app.route('/posts/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_post(id):
    """Edit a post's body and code snippets (only author can edit)"""
    query = "SELECT * FROM posts WHERE id = %s"
    post = execute_query(query, (id,), fetchone=True)
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    if post.get('is_archived', False):
        flash('Cannot edit an archived post', 'danger')
        return redirect(url_for('view_archive'))
    if current_user.id != post['author_id']:
        flash('Only the post author can edit this post', 'error')
        return redirect(url_for('view_post', id=id))
    code_snippets_query = """
        SELECT * FROM code_snippets
        WHERE post_id = %s
        ORDER BY created_at ASC
    """
    existing_code_snippets = execute_query(code_snippets_query, (id,), fetchall=True)
    attachments_query = """
        SELECT a.*, u.username, u.full_name
        FROM attachments a
        JOIN users u ON a.uploaded_by = u.id
        WHERE a.post_id = %s
        ORDER BY a.uploaded_at ASC
    """
    existing_attachments = execute_query(attachments_query, (id,), fetchall=True)
    if request.method == 'POST':
        new_title = request.form.get('title', '')
        new_body = request.form.get('body', '')
        if not new_title or not new_title.strip():
            flash('Title cannot be empty', 'error')
            return render_template('posts/edit_post.html', post=post, code_snippets=existing_code_snippets, attachments=existing_attachments)
        if not new_body or not new_body.strip():
            flash('Body cannot be empty', 'error')
            return render_template('posts/edit_post.html', post=post, code_snippets=existing_code_snippets, attachments=existing_attachments)
        import json
        changes_made = False
        title_changed = new_title != post['title']
        body_changed = new_body != post['body']
        attachment_changes = {
            'deleted': [],
            'added': []
        }
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if title_changed:
                cursor.execute(
                    "UPDATE posts SET title = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (new_title, id)
                )
                changes_made = True
            if body_changed:
                cursor.execute(
                    "UPDATE posts SET body = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (new_body, id)
                )
                changes_made = True
            existing_snippet_ids = {snippet['id'] for snippet in existing_code_snippets}
            updated_snippet_ids = set()

            snippet_count = 0
            while f'code_content_{snippet_count}' in request.form:
                code_content = request.form.get(f'code_content_{snippet_count}', '').strip()
                if code_content:
                    code_title = request.form.get(f'code_title_{snippet_count}', '').strip()
                    code_language = request.form.get(f'code_language_{snippet_count}', 'text')
                    code_id = request.form.get(f'code_id_{snippet_count}', '').strip()
                    if code_id and code_id.isdigit():
                        snippet_id = int(code_id)
                        updated_snippet_ids.add(snippet_id)
                        old_snippet = next((s for s in existing_code_snippets if s['id'] == snippet_id), None)
                        if old_snippet and (old_snippet['code'] != code_content or old_snippet['title'] != code_title or old_snippet['language'] != code_language):
                            cursor.execute("""
                                UPDATE code_snippets
                                SET title = %s, code = %s, language = %s
                                WHERE id = %s AND post_id = %s
                            """, (code_title, code_content, code_language, snippet_id, id))
                            changes_made=True
                    else:
                        cursor.execute("""
                            INSERT INTO code_snippets (post_id, title, code, language)
                            VALUES (%s, %s, %s, %s)
                        """, (id, code_title, code_content, code_language))
                        changes_made = True
                snippet_count += 1
            deleted_snippets = existing_snippet_ids - updated_snippet_ids
            for snippet_id in deleted_snippets:
                print(f"DEBUG - Deleting snippet {snippet_id}")
                cursor.execute("DELETE FROM code_snippets WHERE id = %s AND post_id = %s", (snippet_id, id))
                changes_made = True
            
            delete_attachment_ids = request.form.getlist('delete_attachment')
            if delete_attachment_ids:
                for attachment_id in delete_attachment_ids:
                    try:
                        attachment_id = int(attachment_id)
                        cursor.execute("SELECT filename, original_filename FROM attachments WHERE id = %s AND post_id = %s", (attachment_id, id))
                        result = cursor.fetchone()
                        if result:
                            filename = result[0]
                            original_filename = result[1]
                            attachment_changes['deleted'].append(original_filename)
                            cursor.execute("DELETE FROM attachments WHERE id = %s AND post_id = %s", (attachment_id, id))
                            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                            if os.path.exists(filepath):
                                os.remove(filepath)
                            changes_made = True
                    except (ValueError, Exception) as e:
                        print(f"Error deleting attachment: {e}")
            if 'new_attachments' in request.files:
                files = request.files.getlist('new_attachments')
                for file in files:
                    if file and file.filename and allowed_file(file.filename):
                        original_filename = secure_filename(file.filename)
                        unique_filename = f"{uuid.uuid4()}_{original_filename}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        file.save(filepath)
                        file_size = os.path.getsize(filepath)
                        cursor.execute("""
                            INSERT INTO attachments (post_id, filename, original_filename, file_size, mime_type, uploaded_by)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (id, unique_filename, original_filename, file_size, file.content_type, current_user.id))
                        attachment_changes['added'].append(original_filename)
                        changes_made = True
            if title_changed or body_changed:
                cursor.execute(
                    "INSERT INTO post_edits (post_id, old_title, old_body, edited_by) VALUES (%s, %s, %s, %s)",
                    (id, post['title'], post['body'], current_user.id)
                )
            if attachment_changes['deleted'] or attachment_changes['added']:
                cursor.execute(
                    "INSERT INTO post_edits (post_id, old_title, old_body, edited_by, attachment_changes) VALUES (%s, %s, %s, %s, %s)",
                    (id, post['title'], post['body'], current_user.id, json.dumps(attachment_changes))
                )
            conn.commit()
            cursor.close()
        if not changes_made:
            flash('No changes made', 'warning')
        else:
            flash('Post updated successfully', 'success')
        return redirect(url_for('view_post', id=id))
    return render_template('posts/edit_post.html', post=post, code_snippets=existing_code_snippets, attachments=existing_attachments)

@app.route('/posts/<int:id>/history')
def post_history(id):
    """View edit history for the post"""
    post_query = """
        SELECT p.*, u.username, u.full_name
        FROM posts p
        JOIN users u ON p.author_id = u.id
        WHERE p.id = %s
    """
    post = execute_query(post_query, (id,), fetchone=True)
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    history_query = """
        SELECT
            pe.*,
            u.username,
            u.full_name
        FROM post_edits pe
        JOIN users u ON pe.edited_by = u.id
        WHERE pe.post_id = %s
        ORDER BY pe.edited_at DESC
    """
    edits = execute_query(history_query, (id,), fetchall=True)
    return render_template('posts/post_history.html', post=post, edits=edits)

@app.route('/uploads/<filename>')
@login_required
def download_file(filename):
    """Serve uploaded files"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)

@app.route('/api/datasets/search')
def search_datasets():
    """API endpoint for searching datasets (for Select2)"""
    query = request.args.get('q', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 10
    if not query:
        sql = """
            SELECT id, name, slug
            FROM datasets
            ORDER BY name
            LIMIT %s OFFSET %s
        """
        datasets = execute_query(sql, (per_page, (page - 1) * per_page), fetchall=True)
    else:
        sql = """
            SELECT id, name, slug
            FROM datasets
            WHERE name ILIKE %s
            ORDER BY name
            LIMIT %s OFFSET %s
        """
        datasets = execute_query(sql, (f'%{query}%', per_page, (page - 1) * per_page), fetchall=True)
    results = [{'id': d['id'], 'text': d['name']} for d in datasets]
    return jsonify({
        'results': results,
        'pagination': {'more' : len(results) == per_page}
    })

@app.route('/browse-datasets')
def browse_datasets():
    """Browse all datasets alphabetically"""
    query = """
        SELECT
            d.id,
            d.name,
            d.slug,
            d.url,
            d.created_at,
            COUNT(CASE WHEN p.is_archived = FALSE THEN pd.post_id END) as post_count
        FROM datasets d
        LEFT JOIN post_datasets pd ON d.id = pd.dataset_id
        LEFT JOIN posts p ON pd.post_id = p.id
        GROUP BY d.id, d.name, d.slug, d.url, d.created_at
    """
    datasets = execute_query(query, fetchall=True)
    from collections import defaultdict
    grouped = defaultdict(list)
    for dataset in datasets:
        first_char = dataset['name'][0].upper()
        if first_char.isdigit():
            first_char = '#'
        grouped[first_char].append(dataset)
    grouped_sorted = dict(sorted(grouped.items()))
    available_letters = list(grouped_sorted.keys())
    return render_template('browse_datasets.html', grouped_datasets=grouped_sorted, available_letters=available_letters)

@app.route('/dataset/<slug>')
def dataset_detail(slug):
    """View posts for a specific dataset"""
    dataset_query = "SELECT * FROM datasets WHERE slug = %s"
    dataset = execute_query(dataset_query, (slug,), fetchone=True)
    if not dataset:
        flash('Dataset not found', 'error')
        return redirect(url_for('home'))
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    from psycopg2.extras import RealDictCursor
    with get_db_connection() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM posts p
            WHERE p.id IN (
                SELECT post_id
                FROM post_datasets
                WHERE dataset_id = %s
            )
            AND p.is_archived = FALSE
        """, (dataset['id'],))
        total_posts = cursor.fetchone()['total']
        cursor.execute("""
            SELECT
                p.*,
                u.username,
                u.full_name,
                (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as comment_count,
                (SELECT MAX(CASE WHEN c.is_accepted THEN 1 ELSE 0 END) FROM comments c WHERE c.post_id = p.id) as has_accepted_answer
            FROM posts p
            JOIN users u ON p.author_id = u.id
            WHERE p.id IN (
                SELECT post_id
                FROM post_datasets
                WHERE dataset_id = %s
            )
            AND p.is_archived = FALSE
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (dataset['id'], per_page, offset))
        posts = cursor.fetchall()
        cursor.close()
    for post in posts:
        tags_query = """
            SELECT t.id, t.name, t.slug
            FROM tags t
            INNER JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
            ORDER BY t.name
        """
        post['tags'] = execute_query(tags_query, (post['id'],), fetchall=True)
    total_pages = (total_posts + per_page - 1) // per_page
    return render_template('dataset_detail.html', dataset=dataset, posts=posts, page=page, total_pages=total_pages)

@app.route('/api/tags/search')
def search_tags():
    """AJAX endpoint for tag search (Select2)"""
    query = request.args.get('q', '')
    page = int(request.args.get('page', 1))
    per_page = 10
    offset = (page - 1) * per_page
    if query:
        search_query = """
            SELECT id, name, slug
            FROM tags
            WHERE name ILIKE %s
            ORDER BY name
            LIMIT %s OFFSET %s
        """
        tags = execute_query(search_query, (f'%{query}%', per_page, offset), fetchall=True)
        count_query = "SELECT COUNT(*) as count FROM tags WHERE name ILIKE %s"
        count_result = execute_query(count_query, (f'%{query}%',), fetchone=True)
    else:
        tags_query = """
            SELECT id, name, slug
            FROM tags
            ORDER BY name
            LIMIT %s OFFSET %s
        """
        tags = execute_query(tags_query, (per_page, offset), fetchall=True)
        count_query = "SELECT COUNT(*) as count FROM tags"
        count_result = execute_query(count_query, fetchone=True)
    total_count = count_result['count'] if count_result else 0
    results = [{'id':tag['id'], 'text':tag['name']} for tag in tags]
    return jsonify({
        'results': results,
        'pagination': {
            'more': (offset + per_page) < total_count
        }
    })

@app.route('/browse-tags')
def browse_tags():
    """Browse all tags alphabetically"""
    query = """
        SELECT
            t.id,
            t.name,
            t.slug,
            t.created_at,
            COUNT(CASE WHEN p.is_archived = FALSE THEN pt.post_id END) as post_count
        FROM tags t
        LEFT JOIN post_tags pt ON t.id = pt.tag_id
        LEFT JOIN posts p ON pt.post_id = p.id
        GROUP BY t.id, t.name, t.slug, t.created_at
        ORDER BY t.name
    """
    tags = execute_query(query, fetchall=True)
    from collections import defaultdict
    grouped = defaultdict(list)
    for tag in tags:
        first_char = tag['name'][0].upper()
        if first_char.isdigit():
            first_char = '#'
        grouped[first_char].append(tag)
    grouped_sorted = dict(sorted(grouped.items()))
    available_letters = list(grouped_sorted.keys())
    return render_template('browse_tags.html', grouped_tags=grouped_sorted, available_letters=available_letters)

@app.route('/tag/<slug>')
def tag_detail(slug):
    """View posts for a specific tag"""
    tag_query = "SELECT * FROM tags  WHERE slug = %s"
    tag = execute_query(tag_query, (slug,), fetchone=True)
    if not tag:
        flash('Tag not found', 'error')
        return redirect(url_for('home'))
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    from psycopg2.extras import RealDictCursor
    with get_db_connection() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM posts p
            WHERE p.id IN (
                SELECT post_id
                FROM post_tags
                WHERE tag_id = %s
            )
            AND p.is_archived = FALSE
        """, (tag['id'],))
        total_posts = cursor.fetchone()['total']
        cursor.execute("""
            SELECT
                p.*,
                u.username,
                u.full_name,
                (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as comment_count,
                (SELECT MAX(CASE WHEN c.is_accepted THEN 1 ELSE 0 END) FROM comments c WHERE c.post_id = p.id) as has_accepted_answer
            FROM posts p
            JOIN users u ON p.author_id = u.id
            WHERE p.id IN (
                SELECT post_id
                FROM post_tags
                WHERE tag_id = %s
            ) 
            AND p.is_archived = FALSE
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (tag['id'], per_page, offset))
        posts = cursor.fetchall()
        cursor.close()
    for post in posts:
        datasets_query = """
            SELECT d.id, d.name, d.slug
            FROM datasets d
            INNER JOIN post_datasets pd ON d.id = pd.dataset_id
            WHERE pd.post_id = %s
            ORDER BY d.name
        """
        post['datasets'] = execute_query(datasets_query, (post['id'],), fetchall=True)
        tags_query = """
            SELECT t.id, t.name, t.slug
            FROM tags t
            INNER JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
            ORDER BY t.name
        """
        post['tags'] = execute_query(tags_query, (post['id'],), fetchall=True)
    total_pages = (total_posts + per_page - 1) // per_page
    return render_template('tag_detail.html', tag=tag, posts=posts, total_pages=total_pages, page=page)

@app.route('/author/<username>')
def author_posts(username):
    """View all posts by a specific author"""
    user_query = "SELECT * FROM users WHERE username = %s"
    user = execute_query(user_query, (username,), fetchone=True)
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('home'))
    page = int(request.args.get('page', 1))
    per_page = 10
    offset = (page - 1) * per_page
    from psycopg2.extras import RealDictCursor
    with get_db_connection() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM posts p
            WHERE p.author_id = %s AND p.is_archived = FALSE
        """, (user['id'],))
        total_posts = cursor.fetchone()['total']
        cursor.execute("""
            SELECT
                p.*,
                u.username,
                u.full_name,
            (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as comment_count,
            (SELECT MAX(CASE WHEN c.is_accepted THEN 1 ELSE 0 END) FROM comments c WHERE c.post_id = p.id) as has_accepted_answer
        FROM posts p
        JOIN users u ON p.author_id = u.id
        WHERE u.username = %s AND p.is_archived = FALSE
        ORDER BY p.created_at DESC
        LIMIT %s OFFSET %s
        """, (username, per_page, offset))
        posts = cursor.fetchall()
        cursor.close()
        
    for post in posts:
        tags_query = """
            SELECT t.id, t.name, t.slug
            FROM tags t
            INNER JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
            ORDER BY t.name
        """
        post['tags'] = execute_query(tags_query, (post['id'],), fetchall=True)
        datasets_query = """
            SELECT d.id, d.name, d.slug
            FROM datasets d
            INNER JOIN post_datasets pd ON d.id = pd.dataset_id
            WHERE pd.post_id = %s
            ORDER BY d.name
        """
        post['datasets'] = execute_query(datasets_query, (post['id'],), fetchall=True)
    question_count = sum(1 for p in posts if p['is_question'])
    post_count = len(posts) - question_count
    total_pages = (total_posts + per_page - 1) // per_page
    return render_template('author_posts.html', user=user, posts=posts, question_count=question_count, post_count=post_count, page=page, total_pages=total_pages)

@app.route('/search')
def search():
    """Search posts by keyword across multiple fields"""
    query = request.args.get('q', '')
    search_type = request.args.get('type', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    exact_match = query.endswith(' ') and query.strip() != ''
    query_trimmed = query.strip()
    if not query_trimmed:
        flash('Please enter a search term', 'warning')
        return redirect(url_for('home'))
    if exact_match:
        search_pattern = f'\\m{query_trimmed}\\M'
        operator = '~*'
    else:
        search_pattern = f'%{query_trimmed}%'
        operator = 'ILIKE'
    sql = f"""
        SELECT DISTINCT
            p.*,
            u.username,
            u.full_name,
            COUNT(DISTINCT c.id) as comment_count,
            MAX(CASE WHEN c.is_accepted = TRUE THEN 1 ELSE 0 END) as has_accepted_answer
        FROM posts p
        JOIN users u ON p.author_id = u.id
        LEFT JOIN comments c ON p.id = c.post_id
        LEFT JOIN post_tags pt ON p.id = pt.post_id
        LEFT JOIN tags t ON pt.tag_id = t.id
        LEFT JOIN post_datasets pd ON p.id = pd.post_id
        LEFT JOIN datasets d ON pd.dataset_id = d.id
        LEFT JOIN code_snippets cs ON p.id = cs.post_id
        WHERE p.is_archived = FALSE AND (
            p.title {operator} %s OR
            p.body {operator} %s OR
            u.username {operator} %s OR
            u.full_name {operator} %s OR
            t.name {operator} %s OR
            d.name {operator} %s OR
            cs.code {operator} %s OR
            cs.title {operator} %s
        )
    """
    params = [search_pattern] * 8
    if search_type == 'question':
        sql += " AND p.is_question = TRUE"
    elif search_type == 'post':
        sql += " AND p.is_question = FALSE"
    sql += """
        GROUP BY p.id, u.username, u.full_name
        ORDER BY p.created_at DESC
    """
    count_results = execute_query(sql, tuple(params), fetchall=True)
    total_results = len(count_results)
    sql += f" LIMIT {per_page} OFFSET {offset}"
    posts = execute_query(sql, tuple(params), fetchall=True)
    for post in posts:
        tags_query = """
            SELECT t.id, t.name, t.slug
            FROM tags t
            INNER JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
            ORDER BY t.name
        """
        post['tags'] = execute_query(tags_query, (post['id'],), fetchall=True)
        datasets_query = """
            SELECT d.id, d.name, d.slug
            FROM datasets d
            INNER JOIN post_datasets pd ON d.id = pd.dataset_id
            WHERE pd.post_id = %s
            ORDER BY d.name
        """
        post['datasets'] = execute_query(datasets_query, (post['id'],), fetchall=True)
    total_pages = (total_results + per_page - 1) // per_page
    return render_template('search_results.html', posts=posts, query=query, search_type=search_type, total_results=total_results, page=page, total_pages=total_pages, exact_match=exact_match)

@app.route('/profile')
@login_required
def profile():
    from psycopg2.extras import RealDictCursor
    with get_db_connection() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT
                p.*,
                u.username,
                u.full_name,
                (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as comment_count,
                (SELECT MAX(CASE WHEN c.is_accepted THEN 1 ELSE 0 END) FROM comments c WHERE c.post_id = p.id) as has_accepted_answer
            FROM posts p
            JOIN users u ON p.author_id = u.id
            WHERE u.id = %s AND p.is_archived = FALSE
            ORDER BY p.created_at DESC
        """, (current_user.id,))
        posts = cursor.fetchall()
        cursor.close()
    for post in posts:
        tags_query = """
            SELECT t.id, t.name, t.slug
            FROM tags t
            INNER JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
            ORDER BY t.name
        """
        post['tags'] = execute_query(tags_query, (post['id'],), fetchall=True)
        datasets_query = """
            SELECT d.id, d.name, d.slug
            FROM datasets d
            INNER JOIN post_datasets pd ON d.id = pd.dataset_id
            WHERE pd.post_id = %s
            ORDER BY d.name
        """
        post['datasets'] = execute_query(datasets_query, (post['id'],), fetchall=True)
    question_count = sum(1 for p in posts if p['is_question'])
    post_count = len(posts) - question_count
    bookmark_count = execute_query(
        """SELECT COUNT(*) as count 
            FROM bookmarks b
            JOIN posts p ON b.post_id = p.id
            WHERE b.user_id = %s AND p.is_archived = FALSE""",
        (current_user.id,),
        fetchone=True
    )['count']
    archive_count = execute_query(
        "SELECT COUNT(*) as count FROM posts WHERE author_id = %s AND is_archived = TRUE",
        (current_user.id,),
        fetchone=True
    )['count']
    return render_template('profile/profile.html', posts=posts, question_count=question_count, post_count=post_count, bookmark_count=bookmark_count, archive_count=archive_count)

@app.route('/bookmarks')
@login_required
def bookmarks():
    """View user's bookmarked posts"""
    from psycopg2.extras import RealDictCursor
    with get_db_connection() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT
                p.*,
                u.username,
                u.full_name,
                (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as comment_count,
                (SELECT MAX(CASE WHEN c.is_accepted THEN 1 ELSE 0 END) FROM comments c WHERE c.post_id = p.id) as has_accepted_answer
            FROM posts p
            JOIN users u ON p.author_id = u.id
            WHERE u.id = %s AND p.is_archived = FALSE
            ORDER BY p.created_at DESC
        """, (current_user.id,))
        posts = cursor.fetchall()
        cursor.execute("""
            SELECT
                p.*,
                u.username,
                u.full_name,
                b.created_at as bookmarked_at,
                (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as comment_count,
                (SELECT MAX(CASE WHEN c.is_accepted THEN 1 ELSE 0 END) FROM comments c WHERE c.post_id = p.id) as has_accepted_answer
            FROM bookmarks b
            JOIN posts p ON b.post_id = p.id
            JOIN users u ON p.author_id = u.id
            WHERE b.user_id = %s AND p.is_archived = FALSE
            ORDER BY b.created_at DESC
        """, (current_user.id,))
        bookmarked_posts = cursor.fetchall()
        cursor.close()
    for post in posts:
        tags_query = """
            SELECT t.id, t.name, t.slug
            FROM tags t
            INNER JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
            ORDER BY t.name
        """
        post['tags'] = execute_query(tags_query, (post['id'],), fetchall=True)
        datasets_query = """
            SELECT d.id, d.name, d.slug
            FROM datasets d
            INNER JOIN post_datasets pd ON d.id = pd.dataset_id
            WHERE pd.post_id = %s
            ORDER BY d.name
        """
        post['datasets'] = execute_query(datasets_query, (post['id'],), fetchall=True)
    for post in bookmarked_posts:
        tags_query = """
            SELECT t.id, t.name, t.slug
            FROM tags t
            INNER JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
            ORDER BY t.name
        """
        post['tags'] = execute_query(tags_query, (post['id'],), fetchall=True)
        datasets_query = """
            SELECT d.id, d.name, d.slug
            FROM datasets d
            INNER JOIN post_datasets pd ON d.id = pd.dataset_id
            WHERE pd.post_id = %s
            ORDER BY d.name
        """
        post['datasets'] = execute_query(datasets_query, (post['id'],), fetchall=True)
    question_count = sum(1 for p in posts if p['is_question'])
    post_count = len(posts) - question_count
    bookmark_count = execute_query(
        """SELECT COUNT(*) as count 
            FROM bookmarks b
            JOIN posts p ON b.post_id = p.id
            WHERE user_id = %s AND p.is_archived = FALSE""",
        (current_user.id,),
        fetchone=True
    )['count']
    archive_count = execute_query(
        "SELECT COUNT(*) as count FROM posts WHERE author_id = %s AND is_archived = TRUE",
        (current_user.id,),
        fetchone=True
    )['count']
    return render_template('profile/bookmarks.html', bookmarked_posts=bookmarked_posts, bookmark_count=bookmark_count, post_count=post_count, question_count=question_count, archive_count=archive_count)
    
@app.route('/posts/<int:post_id>/bookmark', methods=['POST'])
@login_required
def bookmark_post(post_id):
    """Bookmark a post"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO bookmarks (user_id, post_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (current_user.id, post_id)
            )
            conn.commit()
            cursor.close()
        flash('Post bookmarked', 'success')
    except Exception as e:
        flash('Error bookmarking post', 'error')
    return redirect(url_for('view_post', id=post_id))

@app.route('/posts/<int:post_id>/unbookmark', methods=['POST'])
@login_required
def unbookmark_post(post_id):
    """Remove bookmark from a post"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM bookmarks WHERE user_id = %s AND post_id = %s",
                (current_user.id, post_id)
            )
            conn.commit()
            cursor.close()
        flash('Bookmark removed', 'success')
    except Exception as e:
        flash('Error removing bookmarking', 'error')
    return redirect(url_for('view_post', id=post_id))

@app.route('/view_archive')
@login_required
def view_archive():
    """Archive page (ONLY place to see archived posts)"""
    query = """
        SELECT p.*, u.full_name, u.username, 
            (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as comment_count
        FROM posts p
        JOIN users u ON p.author_id = u.id
        WHERE p.author_id = %s AND p.is_archived = TRUE
        ORDER BY p.created_at DESC
    """
    archived_posts = execute_query(query, (current_user.id,), fetchall=True)
    if archived_posts is None:
        archived_posts = []
    for post in archived_posts:
        tag_query = """
            SELECT t.* FROM tags t
            JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
        """
        post['tags'] = execute_query(tag_query, (post['id'],), fetchall=True) or []
        datasets_query = """
            SELECT d.name, d.slug FROM datasets d
            JOIN post_datasets pd ON d.id = pd.dataset_id
            WHERE pd.post_id = %s
        """
        datasets = execute_query(datasets_query, (post['id'],), fetchall=True)
        if  datasets and len(datasets) > 0:
            post['dataset_name'] = datasets[0]['name']
            post['dataset_slug'] = datasets[0]['slug']
        else:
            post['dataset_name'] = None
            post['dataset_slug'] = None
    post_count_query = """
        SELECT COUNT(*) as count FROM posts
        WHERE author_id = %s AND is_question = FALSE AND is_archived = FALSE
    """
    post_count = execute_query(post_count_query, (current_user.id,), fetchone=True)['count']
    question_count_query = """
        SELECT COUNT(*) as count FROM posts 
        WHERE author_id = %s AND is_question = TRUE AND is_archived = FALSE
    """
    question_count = execute_query(question_count_query, (current_user.id,), fetchone=True)['count']
    bookmark_count_query = "SELECT COUNT(*) as count FROM bookmarks WHERE user_id = %s"
    bookmark_count = execute_query(bookmark_count_query, (current_user.id,), fetchone=True)['count']
    archive_count = len(archived_posts)
    return render_template('profile/archives.html', archived_posts=archived_posts, post_count=post_count, question_count=question_count, bookmark_count=bookmark_count, archive_count=archive_count)

@app.route('/post/<int:id>/archive', methods=['POST'])
@login_required
def archive(id):
    """Archive a post"""
    query = "SELECT * FROM posts WHERE id = %s"
    post = execute_query(query, (id,), fetchone=True)
    if post['author_id'] != current_user.id:
        flash('You can only archive your own posts', 'danger')
        return redirect(url_for('home'))
    if post['is_archived']:
        flash('This post is already archived', 'warning')
        return redirect(url_for('view_archive'))
    update_query = "UPDATE posts SET is_archived = TRUE WHERE id = %s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(update_query, (id,))
        conn.commit()
        cursor.close()
    flash('Post archived successfully', 'success')
    return redirect(url_for('home'))

@app.route('/post/<int:id>/unarchive', methods=['POST'])
@login_required
def unarchive(id):
    """Unarchive a post"""
    query = "SELECT * FROM posts WHERE id = %s"
    post = execute_query(query, (id,), fetchone=True)
    if post['author_id'] != current_user.id:
        flash('This is already unarchived', 'warning')
        return redirect(url_for('home'))
    if not post ['is_archived']:
        flash('This post is already unarchived', 'warning')
        return redirect(url_for('view_archive'))
    update_query = "UPDATE posts SET is_archived = FALSE WHERE id = %s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(update_query, (id,))
        conn.commit()
        cursor.close()
    flash('Post unarchived successfully', 'success')
    return redirect(url_for('view_archive'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)