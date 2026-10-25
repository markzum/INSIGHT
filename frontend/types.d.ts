/** Контракт frontend-fixture v3.0. Демоданные не являются ответом работающего API. */
export interface InsightFixture {
  schema_version: '3.0';
  is_demo: true;
  as_of: string;
  disclaimer: string;
  companies: Company[];
  events: InsightEvent[];
}

export interface CompanyFeature {
  field: string;
  label: string;
  value: string | number | boolean;
  unit?: 'RUB' | '%';
  source: string;
  is_demo: true;
}

export interface Company {
  id: string;
  display_name: string;
  short_name: string;
  industry: string;
  description: string;
  features: CompanyFeature[];
  missing_data: string[];
}

export interface EvidenceBasis {
  fact_ids: string[];
  company_feature_refs: string[];
  bank_source_ids: string[];
}

export interface Impact {
  id: string;
  text: string;
  condition: string;
  basis: EvidenceBasis;
}

export interface InsightEvent {
  id: string;
  company_ids: string[];
  title: string;
  category: string;
  category_label: string;
  date: string;
  priority: 'high' | 'medium' | 'low';
  priority_reason: string;
  read_time: number;
  summary: string;
  impact_type: 'risk' | 'opportunity' | 'mixed';
  /** Значения score — редакционные демооценки, а не измеренная вероятность. */
  relevance: Record<string, {
    score: number;
    reason: string;
    feature_refs: string[];
    summary: string;
    role_scores: Record<'financial_director' | 'ceo' | 'treasurer', number>;
    role_summary: Record<'financial_director' | 'ceo' | 'treasurer', string>;
  }>;
  facts: Array<{
    id: string;
    text: string;
    source_id: string;
    evidence_quote: string;
    verification_status: 'demo_only';
  }>;
  bank_view: {
    status: 'not_loaded';
    summary: null;
    source_ids: string[];
    message: string;
  };
  ai_interpretation: {
    is_demo: true;
    status: 'prewritten_demo';
    summary: string;
    basis: EvidenceBasis;
    assumptions: string[];
    risks: Impact[];
    opportunities: Impact[];
    /** Проверяемые промежуточные результаты, не скрытая цепочка рассуждений модели. */
    reasoning_steps: string[];
  };
  actions: Array<{
    id: string;
    title: string;
    owner_role: string;
    reason: string;
    check_before_action: string[];
    related_risk_ids: string[];
    basis: EvidenceBasis;
  }>;
  missing_data: string[];
  sources: Array<{
    id: string;
    type: 'news';
    is_demo: true;
    title: string;
    publisher: string;
    date: string;
    url: null;
    text: string;
  }>;
  /** Ответы заранее подготовлены; интерфейс обязан помечать их как демонстрацию. */
  suggested_questions: Array<{ question: string; answer: string }>;
  verification: { status: 'needs_more_data'; conflicts: string[] };
  analysis_status: 'partial';
}
