import json

class PersonalityConverter:
    """Handles the conversion logic between the layers of the Pessoa Framework."""

    @staticmethod
    def calculate_layer3_params(big_five_scores):
        """
        Calculates Layer 3 behavioral parameters from the 5 core Big Five trait scores (1-5 scale).
        
        Args:
            big_five_scores (dict): Dictionary with keys 'O', 'C', 'E', 'A', 'N' and values 1.0-5.0.
            
        Returns:
            dict: Calculated LLM parameters.
        """
        o = big_five_scores.get('O', 3.0)
        c = big_five_scores.get('C', 3.0)
        e = big_five_scores.get('E', 3.0)
        a = big_five_scores.get('A', 3.0)
        n = big_five_scores.get('N', 3.0)

        # Formula: T = 0.3 + (Openness / 5 * 0.6)
        temperature = round(0.3 + (o / 5.0 * 0.6), 3)

        # Formula: TopP = 0.9 - (Conscientiousness / 5 * 0.2)
        top_p = round(0.9 - (c / 5.0 * 0.2), 3)

        # Formula: Freq Penalty = 0.5 - (Agreeableness / 5 * 0.6)
        # Clamped to 0.18-0.5 for safety
        freq_penalty = round(0.5 - (a / 5.0 * 0.6), 3)
        freq_penalty = max(0.18, min(0.5, freq_penalty))

        # Formula: MaxTokens = 200 + (Extraversion / 5 * 400)
        max_tokens = int(200 + (e / 5.0 * 400))

        # Formula: Confidence = (90 - Neuroticism / 5 * 30) / 100
        confidence = round((90 - (n / 5.0 * 30)) / 100.0, 3)

        return {
            "temperature": temperature,
            "top_p": top_p,
            "frequency_penalty": freq_penalty,
            "max_tokens": max_tokens,
            "confidence_threshold": confidence
        }

if __name__ == "__main__":
    # Test with The Strategist example from PDF
    # O=2.3, C=4.7, E=3.0, A=1.8, N=1.2
    strategist_scores = {'O': 2.3, 'C': 4.7, 'E': 3.0, 'A': 1.8, 'N': 1.2}
    params = PersonalityConverter.calculate_layer3_params(strategist_scores)
    print("Test: The Strategist Parameters")
    print(json.dumps(params, indent=2))
    
    # Validation against PDF:
    # Temperature: 0.576 (Match!)
    # Top-P: 0.712 (Match!)
    # Freq Penalty: 0.284 (Match!)
    # Max Tokens: 440 (Match!)
    # Confidence: 0.828 (Match!)
