// The four existing rating criteria, unchanged in wording. This module defines no new item, scale or criterion.
export const LIKERT_CRITERIA = [
  {
    key: 'personalization_score',
    title: '1. Personalization',
    description: 'How well does this plan adapt to your specific age, 5K personal best, target race goal, and reported injury limitations?',
    labels: ['1 - Very Generic', '2 - Somewhat Generic', '3 - Moderate', '4 - Well Personalized', '5 - Highly Personalized']
  },
  {
    key: 'usefulness_score',
    title: '2. Perceived Usefulness',
    description: 'How practical and helpful is this weekly schedule for guiding your real-world running routine?',
    labels: ['1 - Not Useful', '2 - Slightly Useful', '3 - Moderately Useful', '4 - Very Useful', '5 - Extremely Useful']
  },
  {
    key: 'clarity_score',
    title: '3. Clarity of Workouts & Paces',
    description: 'Are the pace targets, warm-up protocols, execution details, and recovery advice easy to comprehend?',
    labels: ['1 - Very Confusing', '2 - Somewhat Unclear', '3 - Acceptable', '4 - Clear & Direct', '5 - Crystal Clear']
  },
  {
    key: 'confidence_score',
    title: '4. Confidence in Following the Plan',
    description: 'How confident do you feel that you could safely adhere to this plan without suffering overtraining or burnout?',
    labels: ['1 - No Confidence', '2 - Low Confidence', '3 - Neutral', '4 - Confident', '5 - Highly Confident']
  }
];
