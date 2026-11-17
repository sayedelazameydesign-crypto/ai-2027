-- إنشاء قاعدة البيانات والجداول الأساسية
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- جدول المستخدمين
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    is_active BOOLEAN DEFAULT true
);

-- جدول الجلسات
CREATE TABLE user_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ip_address INET,
    user_agent TEXT
);

-- جدول الوكلاء
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    agent_type VARCHAR(50) NOT NULL,
    description TEXT,
    status VARCHAR(20) DEFAULT 'offline',
    config JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat TIMESTAMP,
    performance_metrics JSONB DEFAULT '{}',
    api_key VARCHAR(255)
);

-- جدول المهام
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    assigned_by UUID REFERENCES users(id),
    task_type VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    payload JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'pending',
    priority INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    result JSONB DEFAULT '{}',
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3
);

-- جدول سجلات الوكلاء
CREATE TABLE agent_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- جدول الرسائل والتواصل
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    sender_type VARCHAR(20) NOT NULL, -- 'user' or 'agent'
    sender_id UUID,
    content TEXT NOT NULL,
    message_type VARCHAR(20) DEFAULT 'text',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP
);

-- جدول مراقبة الأداء
CREATE TABLE performance_metrics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    metric_name VARCHAR(100) NOT NULL,
    metric_value FLOAT NOT NULL,
    metric_type VARCHAR(50),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- جدول التعلم والتحسين
CREATE TABLE learning_data (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    interaction_type VARCHAR(50) NOT NULL,
    input_data JSONB NOT NULL,
    output_data JSONB NOT NULL,
    feedback_score INTEGER,
    feedback_type VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- جدول التكامل الخارجي
CREATE TABLE integrations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    service_type VARCHAR(50) NOT NULL,
    config JSONB DEFAULT '{}',
    credentials JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    last_sync TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- جدول إعدادات النظام
CREATE TABLE system_settings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    key VARCHAR(100) UNIQUE NOT NULL,
    value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- إنشاء الفهارس لتحسين الأداء
CREATE INDEX idx_tasks_agent_id ON tasks(agent_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at);
CREATE INDEX idx_agent_logs_agent_id ON agent_logs(agent_id);
CREATE INDEX idx_agent_logs_created_at ON agent_logs(created_at);
CREATE INDEX idx_messages_agent_id ON messages(agent_id);
CREATE INDEX idx_performance_metrics_agent_id ON performance_metrics(agent_id);
CREATE INDEX idx_performance_metrics_timestamp ON performance_metrics(timestamp);
CREATE INDEX idx_users_email ON users(email);

-- إنشاء البيانات الأولية
INSERT INTO system_settings (key, value, description) VALUES
('max_concurrent_tasks', '10', 'الحد الأقصى للمهام المتزامنة'),
('agent_timeout', '300', 'انتهاء مهلة_agent بالثواني'),
('log_retention_days', '30', 'عدد أيام الاحتفاظ بالسجلات'),
('learning_batch_size', '100', 'حجم دفعة التعلم'),
('api_rate_limit', '1000', 'حد معدل API في الساعة');

-- إنشاء المستخدم الإداري الافتراضي
INSERT INTO users (username, email, password_hash, role) VALUES
('admin', 'admin@agentsystem.com', '$2b$10$rQ7Q9vQ7Q9vQ7Q9vQ7Q9vO.0G7Q9vQ7Q9vQ7Q9vQ7Q9vQ7Q9v', 'admin');

-- إنشاء الوكلاء الافتراضية
INSERT INTO agents (name, agent_type, description, status, config) VALUES
('Orchestrator Agent', 'orchestrator', 'وكيل التنسيق الرئيسي لجميع الوكلاء الأخرى', 'online', '{"max_tasks": 100, "queue_priority": "high"}'),
('Customer Support Agent', 'customer_support', 'وكيل دعم العملاء والرد على الاستفسارات', 'offline', '{"response_template": "friendly", "escalation_rules": true}'),
('Analytics Agent', 'analytics', 'وكيل تحليل البيانات وإعداد التقارير', 'offline', '{"refresh_interval": 300, "chart_types": ["line", "bar", "pie"]}'),
('Content Generator Agent', 'content_generator', 'وكيل إنشاء المحتوى والنصوص', 'offline', '{"tone": "professional", "languages": ["ar", "en"]}'),
('Monitoring Agent', 'monitoring', 'وكيل مراقبة النظام والسجلات', 'online', '{"log_level": "info", "alert_threshold": 80}'),
('Security Agent', 'security', 'وكيل الأمان والتحكم في الوصول', 'online', '{"max_attempts": 5, "lockout_duration": 900}'),
('Integration Agent', 'integration', 'وكيل التكامل مع الأنظمة الخارجية', 'offline', '{"retry_attempts": 3, "timeout": 30}'),
('Learning Agent', 'learning', 'وكيل التعلم والتحسين', 'offline', '{"learning_rate": 0.01, "batch_size": 32}'),
('UX Advisor Agent', 'ux_advisor', 'وكيل تجربة المستخدم والاستشارات', 'offline', '{"feedback_channels": ["form", "chat"], "anonymize": true}'),
('DevOps Agent', 'devops', 'وكيل الدعم التقني والعمليات', 'online', '{"deployment_env": "development", "backup_frequency": "daily"}');

-- إنشاء عمليات التحديث التلقائي
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_agents_updated_at BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_integrations_updated_at BEFORE UPDATE ON integrations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_system_settings_updated_at BEFORE UPDATE ON system_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();