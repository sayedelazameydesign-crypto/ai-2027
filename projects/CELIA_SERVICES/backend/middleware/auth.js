const jwt = require('jsonwebtoken');
const databaseService = require('../services/database');
const redisService = require('../services/redis');
const winston = require('winston');

const authMiddleware = async (req, res, next) => {
  try {
    const token = req.header('Authorization')?.replace('Bearer ', '');
    
    if (!token) {
      return res.status(401).json({
        error: 'Access denied. No token provided.',
        code: 'NO_TOKEN'
      });
    }
    
    // Verify JWT token
    const decoded = jwt.verify(token, process.env.JWT_SECRET);
    
    // Get user from database
    const userResult = await databaseService.query(`
      SELECT id, username, email, role, is_active 
      FROM users 
      WHERE id = $1 AND is_active = true
    `, [decoded.userId]);
    
    if (userResult.rows.length === 0) {
      return res.status(401).json({
        error: 'Invalid token. User not found.',
        code: 'INVALID_USER'
      });
    }
    
    const user = userResult.rows[0];
    
    // Check if session is still valid in Redis
    const sessionData = await redisService.getUserSession(user.id);
    if (!sessionData || sessionData.token !== token) {
      return res.status(401).json({
        error: 'Session expired. Please login again.',
        code: 'SESSION_EXPIRED'
      });
    }
    
    // Add user to request object
    req.user = user;
    req.token = token;
    
    // Update last activity
    await databaseService.query(`
      UPDATE users 
      SET last_login = NOW() 
      WHERE id = $1
    `, [user.id]);
    
    // Update session activity
    await redisService.setUserSession(user.id, {
      ...sessionData,
      lastActivity: new Date().toISOString()
    }, 3600); // Extend session
    
    next();
  } catch (error) {
    winston.error('Authentication error:', error);
    
    if (error.name === 'TokenExpiredError') {
      return res.status(401).json({
        error: 'Token has expired.',
        code: 'TOKEN_EXPIRED'
      });
    }
    
    if (error.name === 'JsonWebTokenError') {
      return res.status(401).json({
        error: 'Invalid token.',
        code: 'INVALID_TOKEN'
      });
    }
    
    return res.status(500).json({
      error: 'Internal server error during authentication.',
      code: 'AUTH_ERROR'
    });
  }
};

// Role-based authorization middleware
const authorize = (roles = []) => {
  return (req, res, next) => {
    if (!req.user) {
      return res.status(401).json({
        error: 'Authentication required.',
        code: 'AUTH_REQUIRED'
      });
    }
    
    if (roles.length && !roles.includes(req.user.role)) {
      return res.status(403).json({
        error: 'Insufficient permissions.',
        code: 'INSUFFICIENT_PERMISSIONS',
        required: roles,
        current: req.user.role
      });
    }
    
    next();
  };
};

// Agent-specific authorization
const authorizeAgent = (agentTypes = []) => {
  return async (req, res, next) => {
    try {
      const agentId = req.params.agentId || req.body.agent_id || req.query.agentId;
      
      if (!agentId) {
        return res.status(400).json({
          error: 'Agent ID is required.',
          code: 'AGENT_ID_REQUIRED'
        });
      }
      
      // Check if agent exists and is accessible
      const agentResult = await databaseService.query(`
        SELECT * FROM agents WHERE id = $1
      `, [agentId]);
      
      if (agentResult.rows.length === 0) {
        return res.status(404).json({
          error: 'Agent not found.',
          code: 'AGENT_NOT_FOUND'
        });
      }
      
      const agent = agentResult.rows[0];
      
      // Check role-based access
      if (req.user.role === 'user' && agent.agent_type !== 'customer_support') {
        return res.status(403).json({
          error: 'Access denied. Insufficient permissions for this agent.',
          code: 'AGENT_ACCESS_DENIED'
        });
      }
      
      req.agent = agent;
      next();
    } catch (error) {
      winston.error('Agent authorization error:', error);
      return res.status(500).json({
        error: 'Internal server error during agent authorization.',
        code: 'AGENT_AUTH_ERROR'
      });
    }
  };
};

// Rate limiting per user
const rateLimitByUser = (windowMs = 15 * 60 * 1000, max = 100) => {
  return async (req, res, next) => {
    if (!req.user) {
      return next();
    }
    
    const key = `rate_limit:user:${req.user.id}`;
    const limitInfo = await redisService.incrementRateLimit(key, windowMs, max);
    
    // Add rate limit headers
    res.set({
      'X-RateLimit-Limit': limitInfo.limit,
      'X-RateLimit-Remaining': Math.max(0, limitInfo.limit - limitInfo.count),
      'X-RateLimit-Reset': new Date(limitInfo.resetTime)
    });
    
    if (limitInfo.count > max) {
      return res.status(429).json({
        error: 'Too many requests. Please try again later.',
        code: 'RATE_LIMIT_EXCEEDED',
        retryAfter: Math.ceil((limitInfo.resetTime - Date.now()) / 1000)
      });
    }
    
    next();
  };
};

// IP-based rate limiting
const rateLimitByIP = (windowMs = 15 * 60 * 1000, max = 1000) => {
  return async (req, res, next) => {
    const clientIP = req.ip || req.connection.remoteAddress || req.socket.remoteAddress;
    const key = `rate_limit:ip:${clientIP}`;
    const limitInfo = await redisService.incrementRateLimit(key, windowMs, max);
    
    // Add rate limit headers
    res.set({
      'X-RateLimit-Limit': limitInfo.limit,
      'X-RateLimit-Remaining': Math.max(0, limitInfo.limit - limitInfo.count),
      'X-RateLimit-Reset': new Date(limitInfo.resetTime)
    });
    
    if (limitInfo.count > max) {
      return res.status(429).json({
        error: 'Too many requests from this IP. Please try again later.',
        code: 'IP_RATE_LIMIT_EXCEEDED',
        retryAfter: Math.ceil((limitInfo.resetTime - Date.now()) / 1000)
      });
    }
    
    next();
  };
};

// Input validation middleware
const validateInput = (schema) => {
  return (req, res, next) => {
    const { error } = schema.validate(req.body);
    
    if (error) {
      return res.status(400).json({
        error: 'Validation failed.',
        code: 'VALIDATION_ERROR',
        details: error.details.map(detail => ({
          field: detail.path.join('.'),
          message: detail.message
        }))
      });
    }
    
    next();
  };
};

// API key validation for external integrations
const validateApiKey = async (req, res, next) => {
  try {
    const apiKey = req.header('X-API-Key') || req.query.api_key;
    
    if (!apiKey) {
      return res.status(401).json({
        error: 'API key is required.',
        code: 'API_KEY_REQUIRED'
      });
    }
    
    // Validate API key against database
    const integrationResult = await databaseService.query(`
      SELECT * FROM integrations 
      WHERE api_key = $1 AND is_active = true
    `, [apiKey]);
    
    if (integrationResult.rows.length === 0) {
      return res.status(401).json({
        error: 'Invalid API key.',
        code: 'INVALID_API_KEY'
      });
    }
    
    const integration = integrationResult.rows[0];
    req.integration = integration;
    
    next();
  } catch (error) {
    winston.error('API key validation error:', error);
    return res.status(500).json({
      error: 'Internal server error during API key validation.',
      code: 'API_KEY_ERROR'
    });
  }
};

// Agent activity logging
const logAgentActivity = (action) => {
  return async (req, res, next) => {
    try {
      const originalSend = res.send;
      
      res.send = function(data) {
        // Log activity after response is sent
        setImmediate(async () => {
          try {
            const agentId = req.params.agentId || req.body.agent_id;
            
            if (agentId) {
              await databaseService.createLog(
                agentId,
                'info',
                `Agent ${action}: ${req.method} ${req.path}`,
                {
                  userId: req.user?.id,
                  userAgent: req.get('User-Agent'),
                  ip: req.ip,
                  statusCode: res.statusCode,
                  responseTime: Date.now() - req.startTime
                }
              );
            }
          } catch (logError) {
            winston.error('Failed to log agent activity:', logError);
          }
        });
        
        return originalSend.call(this, data);
      };
      
      next();
    } catch (error) {
      winston.error('Agent activity logging error:', error);
      next(); // Continue regardless of logging errors
    }
  };
};

// CORS preflight handler
const handlePreflight = (req, res, next) => {
  res.header('Access-Control-Allow-Origin', req.get('Origin') || '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-Key, X-Requested-With');
  res.header('Access-Control-Allow-Credentials', 'true');
  res.header('Access-Control-Max-Age', '86400');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  next();
};

// Security headers
const securityHeaders = (req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('X-XSS-Protection', '1; mode=block');
  res.setHeader('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';");
  
  next();
};

module.exports = {
  authMiddleware,
  authorize,
  authorizeAgent,
  rateLimitByUser,
  rateLimitByIP,
  validateInput,
  validateApiKey,
  logAgentActivity,
  handlePreflight,
  securityHeaders
};