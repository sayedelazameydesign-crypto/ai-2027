const EventEmitter = require('events');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');

class SecurityAgent extends EventEmitter {
  constructor() {
    super();
    this.id = 'security-agent';
    this.name = 'Security Agent';
    this.type = 'security';
    this.description = 'وكيل الأمان والتحكم في الوصول';
    this.status = 'offline';
    this.failedAttempts = new Map();
    this.activeSessions = new Map();
  }

  async initialize() {
    this.status = 'online';
    this.emit('status:changed', 'online');
    console.log('✅ Security Agent initialized');
  }

  async processTask(taskData) {
    const { type, data } = taskData;
    switch (type) {
      case 'authenticate': return await this.authenticate(data);
      case 'authorize': return await this.authorize(data);
      case 'validate_token': return await this.validateToken(data);
      case 'create_session': return await this.createSession(data);
      case 'terminate_session': return await this.terminateSession(data);
      case 'check_permissions': return await this.checkPermissions(data);
      default: throw new Error('Unsupported task type');
    }
  }

  async authenticate(data) {
    const { username, password, ip_address } = data;
    
    // Check for brute force attempts
    if (this.isRateLimited(ip_address)) {
      return { success: false, error: 'Too many failed attempts' };
    }
    
    // Simulate authentication
    const isValid = Math.random() > 0.2; // 80% success rate
    
    if (isValid) {
      this.clearFailedAttempts(ip_address);
      const token = jwt.sign({ user: username }, process.env.JWT_SECRET, { expiresIn: '24h' });
      return { success: true, token, user: { username, role: 'user' } };
    } else {
      this.recordFailedAttempt(ip_address);
      return { success: false, error: 'Invalid credentials' };
    }
  }

  async authorize(data) {
    const { user, resource, action } = data;
    // Simple authorization logic
    const permissions = {
      'admin': ['read', 'write', 'delete', 'manage'],
      'user': ['read', 'write']
    };
    
    const userPermissions = permissions[user.role] || [];
    const authorized = userPermissions.includes(action);
    
    return { authorized, required_permission: action };
  }

  async validateToken(data) {
    const { token } = data;
    try {
      const decoded = jwt.verify(token, process.env.JWT_SECRET);
      return { valid: true, user: decoded.user };
    } catch (error) {
      return { valid: false, error: error.message };
    }
  }

  async createSession(data) {
    const { user_id, ip_address, user_agent } = data;
    const sessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    
    this.activeSessions.set(sessionId, {
      user_id,
      ip_address,
      user_agent,
      created_at: new Date().toISOString(),
      last_activity: new Date().toISOString()
    });
    
    return { session_id: sessionId, expires_in: 86400 };
  }

  async terminateSession(data) {
    const { session_id } = data;
    this.activeSessions.delete(session_id);
    return { terminated: true };
  }

  async checkPermissions(data) {
    const { user, action } = data;
    const rolePermissions = {
      'admin': { '*': true },
      'user': { 
        'read_own_data': true,
        'write_own_data': true,
        'read_public_data': true
      }
    };
    
    return {
      allowed: rolePermissions[user.role]?.[action] || false,
      role: user.role
    };
  }

  isRateLimited(ip) {
    const attempts = this.failedAttempts.get(ip) || 0;
    return attempts >= 5;
  }

  recordFailedAttempt(ip) {
    const current = this.failedAttempts.get(ip) || 0;
    this.failedAttempts.set(ip, current + 1);
  }

  clearFailedAttempts(ip) {
    this.failedAttempts.delete(ip);
  }

  getId() { return this.id; }
  getName() { return this.name; }
  getType() { return this.type; }
  getDescription() { return this.description; }
  getConfiguration() { return { max_attempts: 5, lockout_duration: 900 }; }
  setConfiguration(config) {}
  async setStatus(newStatus) { this.status = newStatus; }
  async getStatus() { return this.status; }
  async collectMetrics() { return { active_sessions: this.activeSessions.size, failed_attempts: this.failedAttempts.size }; }
  async canAcceptTasks() { return this.status === 'online'; }
  needsTaskQueue() { return false; }
  getCapabilities() { return ['authentication', 'authorization', 'session_management', 'security_monitoring']; }
  getVersion() { return '1.0.0'; }
  async stop() { this.status = 'offline'; }
}

module.exports = SecurityAgent;