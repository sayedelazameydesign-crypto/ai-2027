const redis = require('redis');
const winston = require('winston');
const EventEmitter = require('events');

class RedisService extends EventEmitter {
  constructor() {
    super();
    this.client = null;
    this.subscriber = null;
    this.publisher = null;
    this.isInitialized = false;
  }

  async initialize() {
    try {
      const redisUrl = process.env.REDIS_URL || 'redis://localhost:6379';
      
      // Create main client
      this.client = redis.createClient({
        url: redisUrl,
        retry_strategy: (options) => {
          if (options.error && options.error.code === 'ECONNREFUSED') {
            return new Error('The Redis server refused the connection');
          }
          if (options.total_retry_time > 1000 * 60 * 60) {
            return new Error('Retry time exhausted');
          }
          if (options.attempt > 10) {
            return undefined;
          }
          return Math.min(options.attempt * 100, 3000);
        }
      });

      // Create subscriber and publisher for pub/sub
      this.subscriber = redis.createClient({ url: redisUrl });
      this.publisher = redis.createClient({ url: redisUrl });

      // Connect all clients
      await Promise.all([
        this.connectClient(this.client, 'main'),
        this.connectClient(this.subscriber, 'subscriber'),
        this.connectClient(this.publisher, 'publisher')
      ]);

      this.setupEventListeners();
      this.isInitialized = true;
      this.emit('connected');

      winston.info('✅ Redis service initialized successfully');
    } catch (error) {
      winston.error('❌ Redis initialization failed:', error);
      throw error;
    }
  }

  async connectClient(client, type) {
    await client.connect();
    winston.info(`🔌 Redis ${type} client connected`);
  }

  setupEventListeners() {
    // Handle client events
    [this.client, this.subscriber, this.publisher].forEach(client => {
      client.on('error', (err) => {
        winston.error('Redis client error:', err);
        this.emit('error', err);
      });

      client.on('connect', () => {
        winston.info(`Redis ${client === this.client ? 'main' : client === this.subscriber ? 'subscriber' : 'publisher'} client connected`);
        this.emit(`${client === this.client ? 'main' : client === this.subscriber ? 'subscriber' : 'publisher'}-connected`);
      });

      client.on('end', () => {
        winston.warn(`Redis ${client === this.client ? 'main' : client === this.subscriber ? 'subscriber' : 'publisher'} client disconnected`);
        this.emit(`${client === this.client ? 'main' : client === this.subscriber ? 'subscriber' : 'publisher'}-disconnected`);
      });
    });
  }

  // Basic Redis operations
  async set(key, value, expireSeconds = null) {
    try {
      const serializedValue = typeof value === 'string' ? value : JSON.stringify(value);
      
      if (expireSeconds) {
        await this.client.setEx(key, expireSeconds, serializedValue);
      } else {
        await this.client.set(key, serializedValue);
      }
      
      winston.debug(`🔑 Redis SET: ${key} (expires in ${expireSeconds || 'never'}s)`);
      return true;
    } catch (error) {
      winston.error(`Redis SET error for key ${key}:`, error);
      throw error;
    }
  }

  async get(key) {
    try {
      const value = await this.client.get(key);
      if (!value) return null;
      
      try {
        return JSON.parse(value);
      } catch {
        return value;
      }
    } catch (error) {
      winston.error(`Redis GET error for key ${key}:`, error);
      throw error;
    }
  }

  async del(key) {
    try {
      const result = await this.client.del(key);
      winston.debug(`🗑️ Redis DEL: ${key}`);
      return result;
    } catch (error) {
      winston.error(`Redis DEL error for key ${key}:`, error);
      throw error;
    }
  }

  async exists(key) {
    try {
      return await this.client.exists(key);
    } catch (error) {
      winston.error(`Redis EXISTS error for key ${key}:`, error);
      throw error;
    }
  }

  // Hash operations
  async hSet(key, field, value) {
    try {
      const serializedValue = typeof value === 'string' ? value : JSON.stringify(value);
      return await this.client.hSet(key, field, serializedValue);
    } catch (error) {
      winston.error(`Redis HSET error for key ${key}, field ${field}:`, error);
      throw error;
    }
  }

  async hGet(key, field) {
    try {
      const value = await this.client.hGet(key, field);
      if (!value) return null;
      
      try {
        return JSON.parse(value);
      } catch {
        return value;
      }
    } catch (error) {
      winston.error(`Redis HGET error for key ${key}, field ${field}:`, error);
      throw error;
    }
  }

  async hGetAll(key) {
    try {
      const hash = await this.client.hGetAll(key);
      const result = {};
      
      Object.keys(hash).forEach(field => {
        try {
          result[field] = JSON.parse(hash[field]);
        } catch {
          result[field] = hash[field];
        }
      });
      
      return result;
    } catch (error) {
      winston.error(`Redis HGETALL error for key ${key}:`, error);
      throw error;
    }
  }

  async hDel(key, field) {
    try {
      return await this.client.hDel(key, field);
    } catch (error) {
      winston.error(`Redis HDEL error for key ${key}, field ${field}:`, error);
      throw error;
    }
  }

  // List operations
  async lPush(key, ...values) {
    try {
      const serializedValues = values.map(value => 
        typeof value === 'string' ? value : JSON.stringify(value)
      );
      return await this.client.lPush(key, serializedValues);
    } catch (error) {
      winston.error(`Redis LPUSH error for key ${key}:`, error);
      throw error;
    }
  }

  async rPush(key, ...values) {
    try {
      const serializedValues = values.map(value => 
        typeof value === 'string' ? value : JSON.stringify(value)
      );
      return await this.client.rPush(key, serializedValues);
    } catch (error) {
      winston.error(`Redis RPUSH error for key ${key}:`, error);
      throw error;
    }
  }

  async lPop(key) {
    try {
      const value = await this.client.lPop(key);
      if (!value) return null;
      
      try {
        return JSON.parse(value);
      } catch {
        return value;
      }
    } catch (error) {
      winston.error(`Redis LPOP error for key ${key}:`, error);
      throw error;
    }
  }

  async rPop(key) {
    try {
      const value = await this.client.rPop(key);
      if (!value) return null;
      
      try {
        return JSON.parse(value);
      } catch {
        return value;
      }
    } catch (error) {
      winston.error(`Redis RPOP error for key ${key}:`, error);
      throw error;
    }
  }

  async lRange(key, start = 0, stop = -1) {
    try {
      const values = await this.client.lRange(key, start, stop);
      return values.map(value => {
        try {
          return JSON.parse(value);
        } catch {
          return value;
        }
      });
    } catch (error) {
      winston.error(`Redis LRANGE error for key ${key}:`, error);
      throw error;
    }
  }

  async lLen(key) {
    try {
      return await this.client.lLen(key);
    } catch (error) {
      winston.error(`Redis LLEN error for key ${key}:`, error);
      throw error;
    }
  }

  // Set operations
  async sAdd(key, ...members) {
    try {
      const serializedMembers = members.map(member => 
        typeof member === 'string' ? member : JSON.stringify(member)
      );
      return await this.client.sAdd(key, serializedMembers);
    } catch (error) {
      winston.error(`Redis SADD error for key ${key}:`, error);
      throw error;
    }
  }

  async sMembers(key) {
    try {
      const members = await this.client.sMembers(key);
      return members.map(member => {
        try {
          return JSON.parse(member);
        } catch {
          return member;
        }
      });
    } catch (error) {
      winston.error(`Redis SMEMBERS error for key ${key}:`, error);
      throw error;
    }
  }

  async sIsMember(key, member) {
    try {
      const serializedMember = typeof member === 'string' ? member : JSON.stringify(member);
      return await this.client.sIsMember(key, serializedMember);
    } catch (error) {
      winston.error(`Redis SISMEMBER error for key ${key}:`, error);
      throw error;
    }
  }

  async sRem(key, ...members) {
    try {
      const serializedMembers = members.map(member => 
        typeof member === 'string' ? member : JSON.stringify(member)
      );
      return await this.client.sRem(key, serializedMembers);
    } catch (error) {
      winston.error(`Redis SREM error for key ${key}:`, error);
      throw error;
    }
  }

  // Pub/Sub operations
  async publish(channel, message) {
    try {
      const serializedMessage = typeof message === 'string' ? message : JSON.stringify(message);
      return await this.publisher.publish(channel, serializedMessage);
    } catch (error) {
      winston.error(`Redis PUBLISH error for channel ${channel}:`, error);
      throw error;
    }
  }

  async subscribe(channel, callback) {
    try {
      await this.subscriber.subscribe(channel, (message) => {
        try {
          const parsedMessage = JSON.parse(message);
          callback(parsedMessage);
        } catch {
          callback(message);
        }
      });
    } catch (error) {
      winston.error(`Redis SUBSCRIBE error for channel ${channel}:`, error);
      throw error;
    }
  }

  // Agent-specific operations
  async setAgentStatus(agentId, status, data = {}) {
    const key = `agent:${agentId}:status`;
    const value = {
      status,
      timestamp: new Date().toISOString(),
      ...data
    };
    
    await this.set(key, value, 300); // Expire in 5 minutes
    await this.publish('agent-status-updates', { agentId, status, data });
  }

  async getAgentStatus(agentId) {
    const key = `agent:${agentId}:status`;
    return await this.get(key);
  }

  async setAgentTask(agentId, taskId, taskData) {
    const key = `agent:${agentId}:task:${taskId}`;
    await this.set(key, taskData, 3600); // Expire in 1 hour
  }

  async getAgentTask(agentId, taskId) {
    const key = `agent:${agentId}:task:${taskId}`;
    return await this.get(key);
  }

  async removeAgentTask(agentId, taskId) {
    const key = `agent:${agentId}:task:${taskId}`;
    return await this.del(key);
  }

  async addToAgentQueue(agentType, taskData) {
    const key = `queue:${agentType}`;
    return await this.rPush(key, taskData);
  }

  async getFromAgentQueue(agentType) {
    const key = `queue:${agentType}`;
    return await this.lPop(key);
  }

  async getAgentQueueLength(agentType) {
    const key = `queue:${agentType}`;
    return await this.lLen(key);
  }

  async setUserSession(userId, sessionData, expireSeconds = 3600) {
    const key = `session:${userId}`;
    await this.set(key, sessionData, expireSeconds);
  }

  async getUserSession(userId) {
    const key = `session:${userId}`;
    return await this.get(key);
  }

  async removeUserSession(userId) {
    const key = `session:${userId}`;
    return await this.del(key);
  }

  async setCache(key, data, expireSeconds = 300) {
    return await this.set(`cache:${key}`, data, expireSeconds);
  }

  async getCache(key) {
    const cacheKey = `cache:${key}`;
    return await this.get(cacheKey);
  }

  async invalidateCache(pattern) {
    try {
      const keys = await this.client.keys(`cache:${pattern}`);
      if (keys.length > 0) {
        await this.client.del(keys);
      }
    } catch (error) {
      winston.error(`Cache invalidation error for pattern ${pattern}:`, error);
    }
  }

  // Performance metrics caching
  async cacheMetrics(agentId, metrics, expireSeconds = 60) {
    const key = `metrics:${agentId}`;
    await this.set(key, metrics, expireSeconds);
  }

  async getCachedMetrics(agentId) {
    const key = `metrics:${agentId}`;
    return await this.get(key);
  }

  // Rate limiting
  async incrementRateLimit(key, windowMs = 900000, limit = 100) {
    const counterKey = `rate_limit:${key}`;
    const now = Date.now();
    const window = Math.floor(now / windowMs);
    const windowKey = `${counterKey}:${window}`;
    
    const current = await this.client.incr(windowKey);
    
    if (current === 1) {
      await this.client.expire(windowKey, Math.ceil(windowMs / 1000));
    }
    
    return {
      count: current,
      limit,
      resetTime: (window + 1) * windowMs
    };
  }

  async isRateLimited(key, windowMs = 900000, limit = 100) {
    const result = await this.incrementRateLimit(key, windowMs, limit);
    return result.count > limit;
  }

  // System monitoring
  async getRedisInfo() {
    try {
      const info = await this.client.info();
      return this.parseRedisInfo(info);
    } catch (error) {
      winston.error('Failed to get Redis info:', error);
      return null;
    }
  }

  parseRedisInfo(info) {
    const sections = info.split('\r\n\r\n');
    const result = {};
    
    sections.forEach(section => {
      if (section.trim()) {
        const lines = section.split('\r\n');
        const sectionName = lines[0].replace(/^# /, '');
        const data = {};
        
        for (let i = 1; i < lines.length; i++) {
          const line = lines[i];
          if (line && line.includes(':')) {
            const [key, value] = line.split(':');
            data[key] = value;
          }
        }
        
        result[sectionName] = data;
      }
    });
    
    return result;
  }

  async close() {
    try {
      if (this.client) await this.client.quit();
      if (this.subscriber) await this.subscriber.quit();
      if (this.publisher) await this.publisher.quit();

      this.isInitialized = false;
      this.emit('disconnected');
      
      winston.info('🔌 Redis connections closed');
    } catch (error) {
      winston.error('Error closing Redis connections:', error);
      throw error;
    }
  }

  // Utility methods
  isConnected() {
    return this.isInitialized && this.client && this.subscriber && this.publisher;
  }

  getClient() {
    return this.client;
  }

  getSubscriber() {
    return this.subscriber;
  }

  getPublisher() {
    return this.publisher;
  }
}

// Singleton instance
const redisService = new RedisService();

module.exports = redisService;