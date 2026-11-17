const EventEmitter = require('events');
const winston = require('winston');
const OpenAI = require('openai');
const natural = require('natural');
const sentiment = require('sentiment');

class CustomerSupportAgent extends EventEmitter {
  constructor() {
    super();
    this.id = 'customer-support-agent';
    this.name = 'Customer Support Agent';
    this.type = 'customer_support';
    this.description = 'وكيل دعم العملاء المتخصص في الرد على الاستفسارات وحل المشاكل';
    this.status = 'offline';
    this.configuration = {
      responseTemplate: 'friendly',
      escalationRules: true,
      languages: ['ar', 'en'],
      maxResponseTime: 30,
      sentimentThreshold: -0.5
    };
    
    this.openai = new OpenAI({
      apiKey: process.env.OPENAI_API_KEY
    });
    
    this.nlp = new natural.SentimentAnalyzer('Arabic', natural.PorterStemmer, ['ar']);
    this.sentiment = new sentiment();
    
    this.conversationHistory = new Map();
    this.activeConversations = new Set();
  }

  async initialize() {
    try {
      winston.info('🤖 Initializing Customer Support Agent...');
      
      // Initialize NLP models
      await this.initializeNLP();
      
      // Load conversation templates
      await this.loadResponseTemplates();
      
      this.status = 'online';
      this.emit('status:changed', 'online');
      
      winston.info('✅ Customer Support Agent initialized');
    } catch (error) {
      winston.error('❌ Failed to initialize Customer Support Agent:', error);
      this.status = 'error';
      this.emit('status:changed', 'error', { error: error.message });
      throw error;
    }
  }

  async initializeNLP() {
    // Initialize sentiment analysis for Arabic
    this.sentimentAnalyzer = new natural.SentimentAnalyzer('English', natural.PorterStemmer, ['en']);
    
    // Load common customer support phrases
    this.supportPhrases = {
      greeting: [
        'مرحباً، كيف يمكنني مساعدتك اليوم؟',
        'أهلاً وسهلاً بك، أنا هنا لمساعدتك',
        'مرحباً بك! كيف يمكنني خدمتك؟'
      ],
      apology: [
        'أعتذر عن الإزعاج',
        'أسف لعدم قدرتي على مساعدتك بالشكل المطلوب',
        'آسف للإزعاج الذي حدث'
      ],
      escalation: [
        'سأقوم بتحويلك إلى специалист متخصص',
        'هذه المسألة تتطلب تدخل специалиي',
        'دعني أوجهك للشخص المناسب'
      ]
    };
  }

  async loadResponseTemplates() {
    this.responseTemplates = {
      greeting: {
        ar: 'مرحباً {name}! أنا وكيل دعم العملاء. كيف يمكنني مساعدتك اليوم؟',
        en: 'Hello {name}! I\'m your customer support agent. How can I help you today?'
      },
      complaint: {
        ar: 'أتفهم إزعاجك بخصوص {issue}. دعني أساعدك في حل هذه المشكلة.',
        en: 'I understand your frustration with {issue}. Let me help you resolve this issue.'
      },
      technical_issue: {
        ar: 'بناءً على المشكلة التقنية التي تواجهها، سأبدأ بالحلول الأساسية.',
        en: 'Based on the technical issue you\'re experiencing, let me start with basic troubleshooting.'
      },
      billing_inquiry: {
        ar: 'بخصوص استفسارك عن الفاتورة، دعني أتحقق من التفاصيل.',
        en: 'Regarding your billing inquiry, let me check the details for you.'
      }
    };
  }

  async processTask(taskData) {
    try {
      this.emit('task:started', taskData.id);
      
      const { type, data } = taskData;
      
      switch (type) {
        case 'chat_message':
          return await this.handleChatMessage(data);
        case 'escalate_issue':
          return await this.escalateIssue(data);
        case 'sentiment_analysis':
          return await this.analyzeSentiment(data);
        case 'generate_response':
          return await this.generateResponse(data);
        case 'extract_intent':
          return await this.extractIntent(data);
        default:
          throw new Error(`Unsupported task type: ${type}`);
      }
    } catch (error) {
      this.emit('log', 'error', `Task processing failed: ${error.message}`, { taskData });
      throw error;
    } finally {
      this.emit('task:completed', taskData.id, { status: 'completed' });
    }
  }

  async handleChatMessage(data) {
    const { message, userId, sessionId, language = 'ar' } = data;
    
    // Analyze sentiment
    const sentimentScore = this.sentiment.analyze(message).score;
    
    // Detect intent
    const intent = await this.extractIntent({ text: message });
    
    // Check if escalation is needed
    const needsEscalation = this.shouldEscalate(sentimentScore, intent);
    
    let response;
    
    if (needsEscalation) {
      response = await this.handleEscalation(message, intent, language);
      await this.escalateIssue({ message, intent, sentiment: sentimentScore });
    } else {
      response = await this.generateResponse({
        message,
        intent,
        sentiment: sentimentScore,
        language,
        userId,
        sessionId
      });
    }
    
    // Store conversation
    await this.storeConversation(sessionId, userId, message, response, intent);
    
    // Send real-time response
    this.emit('chat_response', {
      sessionId,
      userId,
      response,
      intent,
      sentiment: sentimentScore
    });
    
    return {
      response,
      intent,
      sentiment: sentimentScore,
      escalated: needsEscalation
    };
  }

  async extractIntent(data) {
    const { text } = data;
    
    // Keywords for intent detection
    const intents = {
      greeting: ['مرحبا', 'السلام', 'أهلا', 'hello', 'hi', 'greetings'],
      complaint: ['شكوى', 'مشكلة', 'غير راضي', 'complaint', 'problem', 'issue'],
      technical: ['خطأ', 'مشكلة تقنية', 'bug', 'technical', 'error'],
      billing: ['فاتورة', 'دفع', 'billing', 'invoice', 'payment'],
      product_info: ['معلومات', 'تفاصيل', 'info', 'details', 'specs'],
      support: ['مساعدة', 'ساعدني', 'help', 'support', 'assistance']
    };
    
    const lowerText = text.toLowerCase();
    let detectedIntents = [];
    
    // Check each intent
    for (const [intent, keywords] of Object.entries(intents)) {
      const match = keywords.some(keyword => lowerText.includes(keyword));
      if (match) {
        detectedIntents.push(intent);
      }
    }
    
    // If no intent detected, analyze with NLP
    if (detectedIntents.length === 0) {
      const tokens = natural.WordTokenizer().tokenize(lowerText);
      const stemmer = natural.PorterStemmer;
      
      for (const token of tokens) {
        const stemmed = stemmer.stem(token);
        for (const [intent, keywords] of Object.entries(intents)) {
          if (keywords.some(keyword => stemmer.stem(keyword.toLowerCase()) === stemmed)) {
            detectedIntents.push(intent);
          }
        }
      }
    }
    
    // Use OpenAI for better intent detection if API key is available
    if (process.env.OPENAI_API_KEY && detectedIntents.length === 0) {
      try {
        const completion = await this.openai.chat.completions.create({
          model: "gpt-3.5-turbo",
          messages: [
            {
              role: "system",
              content: "You are a customer support intent classifier. Analyze the user message and determine the primary intent. Respond with only one word: greeting, complaint, technical, billing, product_info, or support."
            },
            {
              role: "user",
              content: text
            }
          ],
          max_tokens: 10,
          temperature: 0.1
        });
        
        const aiIntent = completion.choices[0].message.content.trim().toLowerCase();
        if (Object.keys(intents).includes(aiIntent)) {
          detectedIntents = [aiIntent];
        }
      } catch (error) {
        winston.warn('OpenAI intent classification failed, using fallback:', error.message);
      }
    }
    
    return {
      primary: detectedIntents[0] || 'general',
      all: detectedIntents,
      confidence: detectedIntents.length > 0 ? 0.8 : 0.3
    };
  }

  async generateResponse(data) {
    const { message, intent, sentiment, language = 'ar', userId, sessionId } = data;
    
    // Get conversation history
    const history = await this.getConversationHistory(sessionId);
    
    let response = '';
    
    // Generate response based on intent
    switch (intent.primary) {
      case 'greeting':
        response = this.getTemplate('greeting', language, { name: userId });
        break;
        
      case 'complaint':
        response = this.getTemplate('complaint', language, { issue: this.extractIssue(message) });
        break;
        
      case 'technical':
        response = await this.handleTechnicalSupport(message, language);
        break;
        
      case 'billing':
        response = await this.handleBillingInquiry(message, language);
        break;
        
      case 'product_info':
        response = await this.handleProductInfoRequest(message, language);
        break;
        
      default:
        response = await this.generateGeneralResponse(message, language, history);
    }
    
    // Enhance response with OpenAI if available
    if (process.env.OPENAI_API_KEY) {
      response = await this.enhanceResponseWithAI(response, message, language, history);
    }
    
    return {
      text: response,
      intent: intent.primary,
      timestamp: new Date().toISOString(),
      language
    };
  }

  getTemplate(templateName, language, variables = {}) {
    const template = this.responseTemplates[templateName]?.[language] || 
                    this.responseTemplates[templateName]?.en || 
                    'Thank you for contacting us.';
    
    // Replace variables in template
    let response = template;
    for (const [key, value] of Object.entries(variables)) {
      response = response.replace(new RegExp(`{${key}}`, 'g'), value);
    }
    
    return response;
  }

  extractIssue(message) {
    const issueKeywords = ['مشكلة', 'خطأ', 'خطأ', 'لا يعمل', 'مشكلتي', 'problem', 'issue', 'error', 'bug'];
    const lowerMessage = message.toLowerCase();
    
    for (const keyword of issueKeywords) {
      if (lowerMessage.includes(keyword)) {
        const startIndex = lowerMessage.indexOf(keyword);
        return message.substring(startIndex, Math.min(startIndex + 100, message.length));
      }
    }
    
    return 'المشكلة المذكورة';
  }

  async handleTechnicalSupport(message, language) {
    const troubleshootingSteps = {
      ar: [
        '1. تأكد من اتصالك بالإنترنت',
        '2. قم بإعادة تشغيل التطبيق',
        '3. تأكد من تحديث التطبيق',
        '4. مسح البيانات المؤقتة للتطبيق'
      ],
      en: [
        '1. Check your internet connection',
        '2. Restart the application',
        '3. Ensure the app is updated',
        '4. Clear app cache'
      ]
    };
    
    const steps = troubleshootingSteps[language] || troubleshootingSteps.en;
    const stepsText = steps.join('\n');
    
    return language === 'ar' 
      ? `لحل المشكلة التقنية، يرجى اتباع الخطوات التالية:\n\n${stepsText}\n\nإذا استمرت المشكلة، يرجى تقديم المزيد من التفاصيل.`
      : `To resolve the technical issue, please follow these steps:\n\n${stepsText}\n\nIf the problem persists, please provide more details.`;
  }

  async handleBillingInquiry(message, language) {
    return language === 'ar'
      ? 'بخصوص استفسارك عن الفاتورة، يمكنني مساعدتك في الحصول على معلومات حول الفواتير والدفع. هل يمكنك تقديم رقم الفاتورة أو تاريخها؟'
      : 'Regarding your billing inquiry, I can help you get information about invoices and payments. Can you provide the invoice number or date?';
  }

  async handleProductInfoRequest(message, language) {
    return language === 'ar'
      ? 'يسعدني مساعدتك في الحصول على معلومات المنتجات. يمكنني تقديم التفاصيل والمواصفات والأسعار. ما المنتج الذي تريد معلومات عنه؟'
      : 'I\'m happy to help you get product information. I can provide details, specifications, and pricing. What product would you like information about?';
  }

  async generateGeneralResponse(message, language, history) {
    const templates = {
      ar: [
        'شكراً لتواصلك معنا. كيف يمكنني مساعدتك بشكل أفضل؟',
        'أقدر استفسارك. دعني أعرض عليك المساعدة.',
        'مرحباً! أنا هنا لمساعدتك في أي استفسار.'
      ],
      en: [
        'Thank you for reaching out. How can I better assist you?',
        'I appreciate your inquiry. Let me offer you some assistance.',
        'Hello! I\'m here to help you with any questions.'
      ]
    };
    
    const responses = templates[language] || templates.en;
    const randomResponse = responses[Math.floor(Math.random() * responses.length)];
    
    return randomResponse;
  }

  async enhanceResponseWithAI(response, originalMessage, language, history) {
    try {
      const context = `Previous conversation:\n${history.map(h => `${h.role}: ${h.content}`).join('\n')}\n\nUser message: ${originalMessage}\nCurrent response: ${response}`;
      
      const completion = await this.openai.chat.completions.create({
        model: "gpt-3.5-turbo",
        messages: [
          {
            role: "system",
            content: `You are an expert customer support agent. Improve the given response to be more helpful, empathetic, and professional. Keep it concise and relevant. Respond in ${language}.`
          },
          {
            role: "user",
            content: context
          }
        ],
        max_tokens: 150,
        temperature: 0.7
      });
      
      const improvedResponse = completion.choices[0].message.content.trim();
      return improvedResponse || response;
    } catch (error) {
      winston.warn('OpenAI response enhancement failed, using original:', error.message);
      return response;
    }
  }

  shouldEscalate(sentimentScore, intent) {
    // Escalate if sentiment is very negative
    if (sentimentScore < this.configuration.sentimentThreshold) {
      return true;
    }
    
    // Escalate if intent is complaint and confidence is high
    if (intent.primary === 'complaint' && intent.confidence > 0.8) {
      return true;
    }
    
    // Escalate if message contains urgency keywords
    const urgentKeywords = ['عاجل', 'urgent', 'emergency', 'critical', 'فوري'];
    const messageContainsUrgent = urgentKeywords.some(keyword => 
      intent.all.some(intent => intent.includes(keyword))
    );
    
    return messageContainsUrgent;
  }

  async escalateIssue(data) {
    const { message, intent, sentiment, userId } = data;
    
    const escalationData = {
      type: 'customer_complaint',
      priority: sentiment < -1 ? 'high' : 'medium',
      category: intent.primary,
      message,
      userId,
      timestamp: new Date().toISOString(),
      sentiment: sentiment
    };
    
    // Log escalation
    this.emit('log', 'warning', `Issue escalated: ${intent.primary}`, escalationData);
    
    // Notify other agents about escalation
    this.emit('escalation', escalationData);
    
    return {
      escalated: true,
      escalationId: `esc_${Date.now()}`,
      priority: escalationData.priority,
      estimatedResponseTime: escalationData.priority === 'high' ? '30 minutes' : '2 hours'
    };
  }

  async storeConversation(sessionId, userId, message, response, intent) {
    const conversation = {
      sessionId,
      userId,
      message,
      response,
      intent: intent.primary,
      timestamp: new Date().toISOString()
    };
    
    // Store in memory
    if (!this.conversationHistory.has(sessionId)) {
      this.conversationHistory.set(sessionId, []);
    }
    
    this.conversationHistory.get(sessionId).push(conversation);
    
    // Keep only last 50 messages per session
    if (this.conversationHistory.get(sessionId).length > 50) {
      this.conversationHistory.set(sessionId, 
        this.conversationHistory.get(sessionId).slice(-50)
      );
    }
    
    this.activeConversations.add(sessionId);
  }

  async getConversationHistory(sessionId) {
    return this.conversationHistory.get(sessionId) || [];
  }

  async analyzeSentiment(data) {
    const { text } = data;
    const analysis = this.sentiment.analyze(text);
    
    return {
      score: analysis.score,
      comparative: analysis.comparative,
      positive: analysis.positive,
      negative: analysis.negative,
      label: this.getSentimentLabel(analysis.score)
    };
  }

  getSentimentLabel(score) {
    if (score > 1) return 'very_positive';
    if (score > 0) return 'positive';
    if (score === 0) return 'neutral';
    if (score > -1) return 'negative';
    return 'very_negative';
  }

  async handleEscalation(message, intent, language) {
    const escalationResponse = language === 'ar'
      ? 'أعتذر، هذه المسألة تحتاج إلى تدخل specialist متخصص. سأقوم بتحويلك إلى فريقنا المتخصص فوراً. شكراً لصبرك.'
      : 'I apologize, this matter requires the attention of a specialist. I\'ll transfer you to our specialized team immediately. Thank you for your patience.';
    
    this.emit('log', 'info', `Escalation response provided for intent: ${intent.primary}`);
    
    return escalationResponse;
  }

  // Health check
  async healthCheck() {
    return {
      healthy: true,
      status: this.status,
      activeConversations: this.activeConversations.size,
      uptime: Date.now() - this.startTime,
      lastActivity: new Date().toISOString()
    };
  }

  // Configuration
  getConfiguration() {
    return this.configuration;
  }

  setConfiguration(config) {
    this.configuration = { ...this.configuration, ...config };
  }

  // Status management
  async setStatus(newStatus) {
    this.status = newStatus;
    this.emit('status:changed', newStatus);
  }

  async getStatus() {
    return this.status;
  }

  // Metrics collection
  async collectMetrics() {
    return {
      active_conversations: {
        value: this.activeConversations.size,
        type: 'gauge',
        description: 'Number of active conversations'
      },
      total_conversations: {
        value: this.conversationHistory.size,
        type: 'gauge',
        description: 'Total conversation sessions'
      },
      avg_response_time: {
        value: 2.5, // Mock data - would calculate actual average
        type: 'gauge',
        description: 'Average response time in seconds'
      },
      escalation_rate: {
        value: 0.15, // Mock data - 15% escalation rate
        type: 'gauge',
        description: 'Rate of escalated conversations'
      }
    };
  }

  async canAcceptTasks() {
    return this.status === 'online' && this.activeConversations.size < 10;
  }

  needsTaskQueue() {
    return true;
  }

  getTaskQueueConfig() {
    return {
      defaultJobOptions: {
        attempts: 2,
        backoff: {
          type: 'exponential',
          delay: 1000,
        },
      }
    };
  }

  getId() {
    return this.id;
  }

  getName() {
    return this.name;
  }

  getType() {
    return this.type;
  }

  getDescription() {
    return this.description;
  }

  getCapabilities() {
    return [
      'chat_processing',
      'sentiment_analysis',
      'intent_classification',
      'escalation',
      'multilingual_support'
    ];
  }

  getVersion() {
    return '1.0.0';
  }

  async stop() {
    this.status = 'offline';
    this.emit('status:changed', 'offline');
    winston.info('🛑 Customer Support Agent stopped');
  }
}

module.exports = CustomerSupportAgent;