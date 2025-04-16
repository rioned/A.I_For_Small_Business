# app.py
from flask import Flask, render_template, request, redirect, url_for, session, Blueprint
import datetime
import os
import uuid
import sqlite3
import bcrypt
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
from werkzeug.routing import PathConverter

# Initialize Flask application
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Essential for session security
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'txt', 'csv', 'png', 'jpg', 'jpeg', 'gif'}
app.config['DATABASE'] = 'mydatabase.db'

# Create admin blueprint
admin_bp = Blueprint('admin', __name__)

# Database helper functions
def get_db_connection():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with app.app_context():
        conn = get_db_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS blog_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                featured_image TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Check for featured_image column
        cursor = conn.execute('PRAGMA table_info(blog_posts)')
        columns = [column[1] for column in cursor.fetchall()]
        if 'featured_image' not in columns:
            conn.execute('ALTER TABLE blog_posts ADD COLUMN featured_image TEXT')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS agent_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                website TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                language TEXT NOT NULL,
                document_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create default admin user if not exists
        admin = conn.execute('SELECT * FROM admin WHERE username = "root"').fetchone()
        if not admin:
            hashed_pw = bcrypt.hashpw('admin123'.encode('utf-8'), bcrypt.gensalt())
            conn.execute('INSERT INTO admin (username, password_hash) VALUES (?, ?)',
                        ('root', hashed_pw))
            conn.commit()
        conn.close()

init_db()

# Context processor for template variables
@app.context_processor
def inject_now():
    return {'current_year': datetime.datetime.utcnow().year}

# Helper functions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Admin routes
@admin_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        try:
            conn = get_db_connection()
            admin = conn.execute('SELECT * FROM admin WHERE username = ?', (username,)).fetchone()
            conn.close()
            
            if admin and bcrypt.checkpw(password.encode('utf-8'), admin['password_hash']):
                session['admin_logged_in'] = True
                return redirect(url_for('admin.admin_dashboard'))
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            
    return render_template('admin/login.html')

@admin_bp.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin.admin_login'))
    
    try:
        conn = get_db_connection()
        total_messages = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
        total_submissions = conn.execute('SELECT COUNT(*) FROM agent_submissions').fetchone()[0]
        total_posts = conn.execute('SELECT COUNT(*) FROM blog_posts').fetchone()[0]
        messages = conn.execute('SELECT * FROM messages ORDER BY created_at DESC LIMIT 10').fetchall()
        submissions = conn.execute('SELECT * FROM agent_submissions ORDER BY created_at DESC LIMIT 10').fetchall()
        posts = conn.execute('SELECT * FROM blog_posts ORDER BY created_at DESC LIMIT 10').fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return redirect(url_for('admin.admin_login'))
    
    return render_template('admin/dashboard.html',
                         total_messages=total_messages,
                         total_submissions=total_submissions,
                         total_posts=total_posts,
                         messages=messages,
                         submissions=submissions,
                         posts=posts)

@admin_bp.route('/admin/delete/<table>/<int:id>')
def delete_entry(table, id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin.admin_login'))
    
    valid_tables = ['messages', 'agent_submissions', 'blog_posts']
    if table not in valid_tables:
        return redirect(url_for('admin.admin_dashboard'))
    
    try:
        conn = get_db_connection()
        conn.execute(f'DELETE FROM {table} WHERE id = ?', (id,))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    
    return redirect(url_for('admin.admin_dashboard'))

@admin_bp.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin.admin_login'))

@admin_bp.route('/admin/posts/new', methods=['GET', 'POST'])
def new_post():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin.admin_login'))

    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        featured_image = request.files.get('featured_image')
        featured_image_path = None

        if featured_image and allowed_file(featured_image.filename):
            filename = secure_filename(featured_image.filename)
            # Updated path handling
            upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'blog_images')
            os.makedirs(upload_dir, exist_ok=True)
            save_path = os.path.join(upload_dir, filename)
            featured_image.save(save_path)
            featured_image_path = os.path.join('uploads/blog_images', filename)  

        try:
            conn = get_db_connection()
            conn.execute('''
                INSERT INTO blog_posts (title, content, featured_image)
                VALUES (?, ?, ?)
            ''', (title, content, featured_image_path))
            conn.commit()
            conn.close()
            return redirect(url_for('admin.admin_dashboard'))
        except sqlite3.Error as e:
            print(f"Database error: {e}")

    return render_template('admin/create_post.html')

@admin_bp.route('/admin/posts/edit/<int:id>', methods=['GET', 'POST'])
def edit_post(id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin.admin_login'))

    conn = get_db_connection()
    post = conn.execute('SELECT * FROM blog_posts WHERE id = ?', (id,)).fetchone()
    conn.close()

    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        featured_image = request.files.get('featured_image')
        featured_image_path = post['featured_image']

        if featured_image and allowed_file(featured_image.filename):
            filename = secure_filename(featured_image.filename)
            upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'blog_images')
            os.makedirs(upload_dir, exist_ok=True)
            featured_image_path = os.path.join(upload_dir, filename)
            featured_image.save(featured_image_path)

        try:
            conn = get_db_connection()
            conn.execute('''
                UPDATE blog_posts
                SET title = ?, content = ?, featured_image = ?
                WHERE id = ?
            ''', (title, content, featured_image_path, id))
            conn.commit()
            conn.close()
            return redirect(url_for('admin.admin_dashboard'))
        except sqlite3.Error as e:
            print(f"Database error: {e}")

    return render_template('admin/edit_post.html', post=post)

class EverythingConverter(PathConverter):
    regex = '.*'

app.url_map.converters['everything'] = EverythingConverter

@app.route('/static/uploads/<everything:path>')
def serve_uploads(path):
    return send_from_directory('static/uploads', path)

# Main application routes
@app.route('/')
def home():
    return render_template('index.html', page_title='Home')

@app.route('/services')
def services():
    service_details = {
        'hotels': {
            'title': 'Hotels & Hospitality',
            'description': 'AI chatbots for guest services, dynamic pricing tools, and personalized booking experiences.',
            'icon': '🏨'
        },
        'marketing': {
            'title': 'Marketing Automation',
            'description': 'AI-driven campaign management, customer segmentation, and performance analytics.',
            'icon': '📊'
        },
        'ecommerce': {
            'title': 'E-commerce Solutions',
            'description': 'Product recommendation engines, inventory management, and AI-powered customer support.',
            'icon': '🛒'
        },
        'engagement': {
            'title': 'Customer Engagement',
            'description': 'Conversational AI platforms, sentiment analysis, and personalized communication strategies.',
            'icon': '💬'
        }
    }
    return render_template('services.html', page_title='Our Services', services=service_details)

@app.route('/about')
def about():
    return render_template('about.html', page_title='About Us')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            email = request.form.get('email')
            message = request.form.get('message')
            
            conn = get_db_connection()
            conn.execute('INSERT INTO messages (name, email, message) VALUES (?, ?, ?)', 
                        (name, email, message))
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            print(f"Database error: {e}")
        
        return redirect(url_for('contact', submitted='true'))
    
    submitted = request.args.get('submitted')
    return render_template('contact.html', page_title='Contact Us', submitted=submitted)

@app.route('/blog')
def blog():
    try:
        conn = get_db_connection()
        posts = conn.execute('SELECT * FROM blog_posts ORDER BY created_at DESC').fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        posts = []
    
    return render_template('blog.html', page_title='Blog', posts=posts)

@app.route('/blog/<int:post_id>')
def blog_post(post_id):
    try:
        conn = get_db_connection()
        post = conn.execute('SELECT * FROM blog_posts WHERE id = ?', (post_id,)).fetchone()
        conn.close()
        
        if not post:
            abort(404)
            
        return render_template('blog_post.html', 
                             page_title=post['title'],
                             post=post)
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        abort(500)

@app.route('/get_started', methods=['GET', 'POST'])
def get_agent():
    if request.method == 'POST':
        file_path = None
        try:
            company_name = request.form.get('company_name')
            website = request.form.get('website')
            email = request.form.get('email')
            phone = request.form.get('phone')
            language = request.form.get('language')
            file = request.files.get('document')

            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)

            conn = get_db_connection()
            conn.execute('''
                INSERT INTO agent_submissions 
                (company_name, website, email, phone, language, document_path)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (company_name, website, email, phone, language, file_path))
            conn.commit()
            conn.close()

            agent_id = str(uuid.uuid4())[:8]
            return redirect(url_for('get_agent', 
                                  submitted='true', 
                                  agent_link=f"{request.host_url}agent/{agent_id}"))
        except (sqlite3.Error, OSError) as e:
            print(f"Error processing submission: {e}")

    submitted = request.args.get('submitted')
    agent_link = request.args.get('agent_link', '#')
    return render_template('get_agent.html', 
                         page_title='Get Started',
                         submitted=submitted,
                         agent_link=agent_link)

@app.route('/video')
def video():
    return render_template('video.html', page_title='Product Video')

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html', page_title='Page Not Found'), 404

app.register_blueprint(admin_bp)

if __name__ == '__main__':
    app.run(debug=True)