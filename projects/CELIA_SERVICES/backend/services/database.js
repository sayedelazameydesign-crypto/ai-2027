const { Pool } = require('pg');
const mongoose = require('mongoose');
const winston = require('winston');
const EventEmitter = require('events');

class DatabaseService extends EventEmitter {
  constructor() {
    super();
    this.postgresPool = null;
    this.mongodbConnection = null;
    this.isInitialized = false;
  }

  async initialize() {
    try {
      // PostgreSQL connection
      this.postgresPool = new Pool({
        connectionString: process.env.DATABASE_URL,
        ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false,
        max: 20,
        idleTimeoutMillis: 30000,
        connectionTimeoutMillis: 2000,
      });

      // Test PostgreSQL connection
      const client = await this.postgresPool.connect();
      await client.query('SELECT NOW()');
      client.release();

      // MongoDB connection
      const mongoUri = process.env.MONGO_URL || 'mongodb://localhost:27017/agent_system';
      this.mongodbConnection = await mongoose.connect(mongoUri, {
        useNewUrlParser: true,
        useUnifiedTopology: true,
      });

      // Setup event listeners
      this.setupEventListeners();

      this.isInitialized = true;
      this.emit('connected');

      winston.info('✅ Database services initialized successfully');
    } catch (error) {
      winston.error('❌ Database initialization failed:', error);
      throw error;
    }
  }

  setupEventListeners() {
    // PostgreSQL error handling
    this.postgresPool.on('error', (err) => {
      winston.error('PostgreSQL pool error:', err);
      this.emit('error', err);
    });

    // MongoDB connection handling
    mongoose.connection.on('connected', () => {
      winston.info('📊 MongoDB connected successfully');
      this.emit('mongodb-connected');
    });

    mongoose.connection.on('error', (err) => {
      winston.error('MongoDB connection error:', err);
      this.emit('mongodb-error', err);
    });

    mongoose.connection.on('disconnected', () => {
      winston.warn('MongoDB disconnected');
      this.emit('mongodb-disconnected');
    });
  }

  // PostgreSQL Methods
  async query(text, params = []) {
    const start = Date.now();
    try {
      const result = await this.postgresPool.query(text, params);
      const duration = Date.now() - start;
      
      winston.debug(`📊 PostgreSQL query executed in ${duration}ms:`, {
        query: text,
        params,
        rows: result.rowCount
      });

      return result;
    } catch (error) {
      winston.error('PostgreSQL query error:', error);
      throw error;
    }
  }

  async getClient() {
    return await this.postgresPool.connect();
  }

  // MongoDB Methods
  getMongooseConnection() {
    return this.mongodbConnection;
  }

  // Transaction support
  async transaction(callback) {
    const client = await this.getClient();
    const session = await client.query('BEGIN');
    
    try {
      const result = await callback(client);
      await client.query('COMMIT');
      return result;
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }

  // Agent-related database methods
  async getAgents() {
    const result = await this.query(`
      SELECT * FROM agents WHERE is_active = true ORDER BY created_at DESC
    `);
    return result.rows;
  }

  async getAgentById(agentId) {
    const result = await this.query(`
      SELECT * FROM agents WHERE id = $1
    `, [agentId]);
    return result.rows[0];
  }

  async updateAgentStatus(agentId, status, metrics = {}) {
    const result = await this.query(`
      UPDATE agents 
      SET status = $2, performance_metrics = $3, last_heartbeat = NOW(), updated_at = NOW()
      WHERE id = $1
      RETURNING *
    `, [agentId, status, JSON.stringify(metrics)]);
    return result.rows[0];
  }

  async createTask(taskData) {
    const result = await this.query(`
      INSERT INTO tasks (agent_id, assigned_by, task_type, title, description, payload, priority)
      VALUES ($1, $2, $3, $4, $5, $6, $7)
      RETURNING *
    `, [
      taskData.agent_id,
      taskData.assigned_by,
      taskData.task_type,
      taskData.title,
      taskData.description,
      JSON.stringify(taskData.payload || {}),
      taskData.priority || 1
    ]);
    return result.rows[0];
  }

  async updateTask(taskId, updates) {
    const fields = [];
    const values = [];
    let paramIndex = 1;

    Object.keys(updates).forEach(key => {
      if (updates[key] !== undefined) {
        fields.push(`${key} = $${paramIndex}`);
        values.push(key === 'payload' || key === 'result' ? JSON.stringify(updates[key]) : updates[key]);
        paramIndex++;
      }
    });

    values.push(taskId);

    const query = `
      UPDATE tasks 
      SET ${fields.join(', ')}, updated_at = NOW()
      WHERE id = $${paramIndex}
      RETURNING *
    `;

    const result = await this.query(query, values);
    return result.rows[0];
  }

  async getTasks(agentId = null, status = null, limit = 100) {
    let query = `
      SELECT t.*, a.name as agent_name, u.username as assigned_by_name
      FROM tasks t
      LEFT JOIN agents a ON t.agent_id = a.id
      LEFT JOIN users u ON t.assigned_by = u.id
    `;
    
    const params = [];
    const conditions = [];

    if (agentId) {
      conditions.push(`t.agent_id = $${params.length + 1}`);
      params.push(agentId);
    }

    if (status) {
      conditions.push(`t.status = $${params.length + 1}`);
      params.push(status);
    }

    if (conditions.length > 0) {
      query += ` WHERE ${conditions.join(' AND ')}`;
    }

    query += ` ORDER BY t.created_at DESC LIMIT $${params.length + 1}`;
    params.push(limit);

    const result = await this.query(query, params);
    return result.rows;
  }

  async createLog(agentId, level, message, metadata = {}) {
    const result = await this.query(`
      INSERT INTO agent_logs (agent_id, level, message, metadata)
      VALUES ($1, $2, $3, $4)
      RETURNING *
    `, [agentId, level, message, JSON.stringify(metadata)]);
    return result.rows[0];
  }

  async createMessage(agentId, senderType, content, messageType = 'text', metadata = {}) {
    const result = await this.query(`
      INSERT INTO messages (agent_id, sender_type, content, message_type, metadata)
      VALUES ($1, $2, $3, $4, $5)
      RETURNING *
    `, [agentId, senderType, content, messageType, JSON.stringify(metadata)]);
    return result.rows[0];
  }

  async getMessages(agentId, limit = 50) {
    const result = await this.query(`
      SELECT * FROM messages
      WHERE agent_id = $1
      ORDER BY created_at DESC
      LIMIT $2
    `, [agentId, limit]);
    return result.rows.reverse(); // Return in chronological order
  }

  async createPerformanceMetric(agentId, metricName, metricValue, metricType = null, metadata = {}) {
    const result = await this.query(`
      INSERT INTO performance_metrics (agent_id, metric_name, metric_value, metric_type, metadata)
      VALUES ($1, $2, $3, $4, $5)
      RETURNING *
    `, [agentId, metricName, metricValue, metricType, JSON.stringify(metadata)]);
    return result.rows[0];
  }

  async getPerformanceMetrics(agentId, metricName = null, timeRange = '24h') {
    let query = `
      SELECT * FROM performance_metrics
      WHERE agent_id = $1
    `;
    
    const params = [agentId];

    if (metricName) {
      query += ` AND metric_name = $${params.length + 1}`;
      params.push(metricName);
    }

    if (timeRange !== 'all') {
      query += ` AND timestamp >= NOW() - INTERVAL '${timeRange}'`;
    }

    query += ` ORDER BY timestamp DESC LIMIT 1000`;

    const result = await this.query(query, params);
    return result.rows;
  }

  async createLearningData(agentId, interactionType, inputData, outputData, feedbackScore = null, feedbackType = null, metadata = {}) {
    const result = await this.query(`
      INSERT INTO learning_data (agent_id, interaction_type, input_data, output_data, feedback_score, feedback_type, metadata)
      VALUES ($1, $2, $3, $4, $5, $6, $7)
      RETURNING *
    `, [agentId, interactionType, JSON.stringify(inputData), JSON.stringify(outputData), feedbackScore, feedbackType, JSON.stringify(metadata)]);
    return result.rows[0];
  }

  async getLearningData(agentId, limit = 100) {
    const result = await this.query(`
      SELECT * FROM learning_data
      WHERE agent_id = $1
      ORDER BY created_at DESC
      LIMIT $2
    `, [agentId, limit]);
    return result.rows;
  }

  // System health and monitoring
  async getSystemHealth() {
    try {
      const [dbStats, taskStats, agentStats] = await Promise.all([
        this.query(`
          SELECT 
            COUNT(*) as total_tasks,
            COUNT(*) FILTER (WHERE status = 'pending') as pending_tasks,
            COUNT(*) FILTER (WHERE status = 'processing') as processing_tasks,
            COUNT(*) FILTER (WHERE status = 'completed') as completed_tasks,
            COUNT(*) FILTER (WHERE status = 'failed') as failed_tasks
          FROM tasks
          WHERE created_at >= NOW() - INTERVAL '24 hours'
        `),
        this.query(`
          SELECT 
            COUNT(*) as total_agents,
            COUNT(*) FILTER (WHERE status = 'online') as online_agents,
            COUNT(*) FILTER (WHERE status = 'offline') as offline_agents,
            COUNT(*) FILTER (WHERE status = 'busy') as busy_agents
          FROM agents
        `),
        this.query(`
          SELECT 
            COUNT(*) as total_logs,
            COUNT(*) FILTER (WHERE level = 'error') as error_logs,
            COUNT(*) FILTER (WHERE level = 'warning') as warning_logs
          FROM agent_logs
          WHERE created_at >= NOW() - INTERVAL '1 hour'
        `)
      ]);

      return {
        database: {
          status: 'healthy',
          tasks: dbStats.rows[0],
          agents: agentStats.rows[0],
          logs: taskStats.rows[0]
        }
      };
    } catch (error) {
      winston.error('Failed to get system health:', error);
      return {
        database: {
          status: 'error',
          error: error.message
        }
      };
    }
  }

  async close() {
    try {
      if (this.postgresPool) {
        await this.postgresPool.end();
      }
      
      if (this.mongodbConnection) {
        await mongoose.disconnect();
      }

      this.isInitialized = false;
      this.emit('disconnected');
      
      winston.info('🔌 Database connections closed');
    } catch (error) {
      winston.error('Error closing database connections:', error);
      throw error;
    }
  }

  // Utility methods
  isConnected() {
    return this.isInitialized && this.postgresPool && mongoose.connection.readyState === 1;
  }

  getPostgresPool() {
    return this.postgresPool;
  }

  getMongoConnection() {
    return this.mongodbConnection;
  }
}

// Singleton instance
const databaseService = new DatabaseService();

module.exports = databaseService;